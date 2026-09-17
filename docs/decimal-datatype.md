# DECIMAL property type for HugeGraph (`feat/decimal-datatype`), 2026-09-14

Why: `PUT /graph/vertices/batch` with `update_strategies: {balance: SUM}` is the one place where the server does
arithmetic on property values. `UpdateStrategy` already computes in `BigDecimal`, but the result is converted back to
the property's data type, so with `DOUBLE` a balance of `10^18 + 1` wei becomes `10^18`. There is no exact numeric
type: `LONG` stops at 2^63 (about 9.2 ETH in wei), `TEXT` fails the strategy's `Number` type check.

Branch: `feat/decimal-datatype` in `SebastianGruza/hugegraph`, one commit on top of apache `master` `60c8803`
(head `a28554e` at the time of writing). Upstream: issue apache/hugegraph#3206 (2026-09-14, design points to confirm); PR apache/hugegraph#3209 (2026-09-16). The fork's
`integration` branch carries the branch for the POC.

## What it adds

- `DataType.DECIMAL(12, "decimal", BigDecimal.class)` in the server enum and in the `hugegraph-struct` copy,
  `isDecimal()`, `valueToDecimal()` (BigDecimal, BigInteger, integral Java numbers exactly; Float/Double via their
  shortest decimal representation; decimal strings). `PropertyKey.Builder.asDecimal()`, REST `data_type: DECIMAL`.
- Encoding (`BytesBuffer`, server core and struct): `vint(len)` + unscaled two's-complement bytes + `vint(scale)`.
  Exact for any precision, scale preserved, 33 bytes for a uint256. Existing encodings untouched.
- JSON: always a plain string on output (`toPlainString()`), a string or a number literal accepted on input.
- `ConditionQuery` compares decimals exactly (not through `double`); the store-side row decoder
  (`GraphStoreIterator`) maps a decimal to a string variant; `OffheapCache` gets the new value type appended.
- `BatchAPI.updateExistElement`: the JSON value is normalised through the property key **before** the
  `update_strategies` strategy runs, on both paths (two entries of one id within a request; request vs stored
  element). Found by the API test: the strategy used to see the raw JSON value, which only worked for the types
  Jackson happens to produce.

## Deliberate limits (to document upstream)

| limit | reason |
|---|---|
| not a vertex primary key, not a sort key, not an index field of any type, not an OLAP write type with an index; the schema builders reject these | no fixed-width byte-order encoding exists for a decimal; `isNumber()` stays `false` on purpose; a primary key would go through `LongEncoding`/`NumericUtil`, which collapses fractions into a double and overflows a long on uint256 (review round 3, imbajin) |
| at most 128 significant digits and an absolute scale of at most 128 (`DataType.DECIMAL_MAX_PRECISION` / `DECIMAL_MAX_SCALE`), checked in `valueToDecimal()` in both copies, so also on the `SUM` result in `BatchAPI` | a value such as `1E+999999999` is a few bytes on disk but a billion characters from `toPlainString()` on every read (review round 2, bitflicker64: OOM at `-Xmx512m`); uint256 with 18 fraction digits is 96 digits, well inside |
| fractions in `PUT .../batch` must be sent as **strings** | the batch request's `properties` map is parsed by Jackson before any schema is known, and a JSON fraction literal becomes a `double` there (`0.000000000000000001` → `1.0E-18`); integral literals are exact (`Integer`/`Long`/`BigInteger`) |
| Gremlin `sum()/max()/min()` work on the returned `BigDecimal` values (TinkerPop `NumberHelper`), but in the server JVM after fetching the elements | unchanged from other types |
| `hugegraph-client` / loader / Hubble do not know the type yet | separate change in the toolchain repository |

## Test suite (all in the branch, run on the lab node with JDK 11 through the CI scripts)

| where | what |
|---|---|
| `unit/core/DataTypeTest` | predicates, `valueToDecimal` for uint256 max, wei scale, integral and binary numbers, invalid strings |
| `unit/serializer/BytesBufferTest` | exact byte layout for `-1.5`, `0`, uint256 max; scale round trip; decimal lists |
| `unit/util/JsonUtilTest` | string on output, string or number on input |
| `core/PropertyKeyCoreTest` | create, value normalisation, `calcSum()`, list cardinality |
| `core/IndexLabelCoreTest` | secondary / range / shard / unique on a decimal all rejected |
| `core/EdgeLabelCoreTest` | decimal sort key rejected, decimal edge property fine |
| `core/VertexCoreTest` | uint256 and 18-fraction-digit values through commit and reload, exact `has()` vs the neighbouring value, `gt/lt/gte`, update, invalid values |
| `api/VertexApiTest` | `PUT /graph/vertices/batch` with `SUM`: `2^256-2` + `1` (number literal), then two entries of one vertex in one request (`"0.000000000000000000"`, `"0.000000000000000001"`), then `BIGGER`; response and `GET` carry the exact string |
| struct `PropertyKeyTest` | groovy schema string, struct `BytesBuffer` round trip |

Results 2026-09-14: struct 4/4; `unit-test` 687/688 (`SecurityManagerTest.testFile` fails identically on plain
master on a Polish locale, unrelated); `core-test,rocksdb` and `core-test,memory` for the four touched classes all
green (383 tests on rocksdb); `api-test,rocksdb` via `run-api-test.sh`: 162 tests, 0 failures.

## Cluster check on HStore and RocksDB (2026-09-14, `cluster/decimal_sum_bench.py`)

Branch dists on the lab (PD + 3 stores, hstore server :8080, rocksdb oracle server :8081), 10 000 `account`
vertices with `balance/hi/lo DECIMAL`, 5 rounds of `PUT /graph/vertices/batch` with
`update_strategies: {balance: SUM, hi: BIGGER, lo: SMALLER}`, random increments up to 2^255 with 18 fraction
digits, 30 % negative, batches of 500, 4 writer threads, 50 accounts per round appearing twice in one request;
2 000 `transfer` edges with a decimal `amount` behind an INT sort key. Every value is then read back and compared
exactly with a Python `Decimal` oracle (`results/decimal/`).

| | hstore | rocksdb |
|---|---|---|
| 50 250 upserts, errors | 0 | 0 |
| mismatches vs the oracle (balance, hi, lo) | **0 / 10 000** | **0 / 10 000** |
| in-request duplicates combined then added | correct | correct |
| decimal edge property through a sort-key traversal | exact | exact |
| decimal sort key / range index | rejected with the intended message | same |
| batch p50 / p99 | 119 / 1 019 ms | 52 / 348 ms |

One trap, independent of the type: the same account updated by **two concurrent requests** is a lost update
(`sum-bench-crossbatch.log`: 47 and 42 of 10 000 accounts wrong, exactly the ones whose duplicate landed in another
batch). Batch upsert is read-modify-write without a per-vertex lock across transactions, for every data type.
An importer accumulating balances must route all updates of one account through one writer, or put them in one
request.

## Review round 1 (2026-09-16) and the E2E suite `cluster/decimal_e2e.py`

The first review of apache/hugegraph#3209 (imbajin, bitflicker64) found three blocking gaps: the `hugegraph-struct`
copy of `PropertyKey` had no DECIMAL conversion (a decimal default value reloaded from JSON as a string could not be
normalised), the new `BigDecimalSerializer` broke the typed GraphSON v2/v3 mappers of gremlin-server
(`serializeWithType` missing, so every BigDecimal Gremlin result failed, including a plain Groovy literal), and an
`OLAP_SECONDARY` decimal key still got a secondary index through `createIndexLabelForOlapPk()`, which skips the
index-label guard. Fixed in commit `9d5eaab` of the branch (struct `valueToDecimal()` + builder + `convSingleValue`
branch; `serializeWithType` with TinkerPop's own `gx:BigDecimal` type id and the plain string in `@value`;
`OLAP_SECONDARY`/`OLAP_RANGE` rejected for decimals in `checkOlap()` and a second guard in
`IndexLabelBuilder.build()`), with a struct test, a GraphSON v1/v2/v3 round-trip unit test and a core OLAP test.

`cluster/decimal_e2e.py <rest_port> <gremlin_port> <tag> [prefix]` runs the same paths end to end against a live
server, on any backend, and compares every value exactly with Python's `Decimal`:

| group | checks |
|---|---|
| R1 schema | DECIMAL keys, a decimal `~default_value` in `user_data`, vertex/edge labels |
| R2 no index | secondary and range index labels on a decimal, decimal sort key: all rejected |
| R3 OLAP | `OLAP_SECONDARY` and `OLAP_RANGE` on a decimal key rejected, `OLAP_COMMON` (no index) allowed |
| R4 REST | create/read uint256 max, 18-digit fraction, negative, zero; `PUT /graph/vertices/batch` with `SUM` and two entries of one vertex in one request; the default value applied to a vertex created without the key |
| R5 Gremlin via the server's `/gremlin` proxy | `values()`, `g.inject(1.5)`, `values()` of the default, `sum()` |
| R6 Gremlin straight on gremlin-server HTTP | the same four with `Accept: application/vnd.gremlin-v1.0+json`, `v2.0`, `v3.0`; typed answers must carry `gx:BigDecimal` and an exact value |

Run on the lab (hstore server on PD + 3 stores, rocksdb server), dists built from the PR head before the fixes
(`a28554e`) and after (`9d5eaab`), logs in `results/decimal/e2e/`:

| | before `a28554e` | after `9d5eaab` |
|---|---|---|
| hstore | 24 PASS, **9 FAIL**, 4 N-A | **33 PASS**, 0 FAIL, 4 N-A |
| rocksdb | 24 PASS, **9 FAIL**, 4 N-A | **33 PASS**, 0 FAIL, 4 N-A |
| the 9 failures before | `OLAP_SECONDARY` accepted on a decimal key; all 8 typed Gremlin answers (v2 and v3 × 4 queries) `HTTP 500 Type id handling not implemented for type java.math.BigDecimal` | |
| typed Gremlin after | `{"@type":"gx:BigDecimal","@value":"115792089237316195423570985008687907853269984665640564039457584007913129639935"}`, `sum()` exact to the 18th fraction digit | |
| N-A | the lab's `gremlin-server.yaml` answers `Accept: application/vnd.gremlin-v1.0+json` with `400 no serializer for requested Accept header`, before and after alike; `/gremlin` through the REST proxy (`application/json`) is untyped and passed on both heads | |

Review round 2 (2026-09-16, bitflicker64): no bound on the exponent (`1E+999999999` stored in a few bytes, a billion characters from `toPlainString()` on every read, OOM at `-Xmx512m`), and the global `BigDecimal` serializer changing existing Gremlin literals from numbers to strings. Fixed by the 128/128 bound above (also for ready-made `BigDecimal`s: `PropertyKey.convValue()` no longer short-circuits decimals, which the struct test caught) and documented in the PR's compatibility note. `decimal_e2e.py` gained three R4 checks (create with `1E+999999999` → 400, 128 significant digits → 201, a `SUM` whose result has 129 digits → 400); on rocksdb with the round-2 dist: 37 PASS, 0 FAIL, 4 N-A (`results/decimal/e2e/after2-rocksdb.log`).

Review round 3 (2026-09-17, imbajin): DECIMAL was still accepted as a vertex primary key, and primary-key ids go through `LongEncoding`/`NumericUtil.numberToSortableLong()`, i.e. through a `double` (`1.000000000000000001` and `…002` collapse into one id) and overflow a `long` on uint256. Rejected in `VertexLabelBuilder.checkPrimaryKeys()` (single and composite keys), `VertexLabelCoreTest.testAddVertexLabelWithDecimalPrimaryKey`, and a new R2 check in `decimal_e2e.py`; on rocksdb with the round-3 dist: 38 PASS, 0 FAIL, 4 N-A (`results/decimal/e2e/after3-rocksdb.log`).

One side effect worth knowing: once a graph contains a DECIMAL property key, a server built without this change
cannot open it (`No enum constant org.apache.hugegraph.type.define.DataType.DECIMAL` at startup). On the lab the
master server on the shared hstore graph only came back after the `d2*_` schema had been deleted through the branch
server. Expected for any new data type, but it belongs in the release notes.

## Not measured yet

Write throughput and disk of a decimal property vs `DOUBLE`/`TEXT` on HStore at scale (the encoding is 3–33 bytes per
value depending on magnitude; expected to sit between the two).
