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
| not a sort key, not an index field of any type, not an OLAP range property; the schema builders reject these | no fixed-width byte-order encoding exists for a decimal; `isNumber()` stays `false` on purpose |
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

## Not measured yet

Write throughput and disk of a decimal property vs `DOUBLE`/`TEXT` on HStore at scale (the encoding is 3–33 bytes per
value depending on magnitude; expected to sit between the two).
