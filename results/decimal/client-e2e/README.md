# hugegraph-client DECIMAL support, E2E against the DECIMAL server (2026-09-20)

Companion change for apache/hugegraph#3209 in apache/hugegraph-toolchain (branch `feat/client-decimal-datatype`
in `SebastianGruza/hugegraph-toolchain`): `DataType.DECIMAL` in both client enums, `PropertyKey.Builder.asDecimal()`,
`BigDecimal` sent as a plain string in every request body and query parameter (a JSON number would be read as a
double), the direct serializer's `BytesBuffer` writes the server layout (unscaled bytes + scale), the loader and the
spark connector convert `decimal` columns to `BigDecimal`, Hubble's Groovy export emits `.asDecimal()`.

## Setup

- Server: `dist-decimal4-rocksdb` on the lab (.235:8081 REST, :8183 Gremlin), built from `feat/decimal-datatype`
  with the review-round patches applied (`server-build.txt`: head, number of uncommitted files, core jar md5).
  Fresh `init-store`, auth off, as in the toolchain CI job.
- Client tests run locally under JDK 11 (the CI Java; Mockito 2.25 and jacoco 0.8.2 do not work on 17) through an
  ssh tunnel (`127.0.0.1:8080` -> `.235:8081`, `8182` -> `8183`), i.e. the unmodified `BaseClientTest` defaults.

## Results

| suite | result | file |
|---|---|---|
| client `UnitTestSuite` (incl. new `DecimalDataTypeTest`, 5 tests) | 77/77 | `client-UnitTestSuite.txt` |
| client `DecimalPropertyApiTest` against the DECIMAL server | 6/6 | `DecimalPropertyApiTest.txt` |
| loader `UnitTestSuite` (incl. new `DataTypeUtilTest`, 2 tests) | 13/13 | `loader-UnitTestSuite.txt` |
| `apache-rat:check`, `checkstyle:check` (client) | clean | |
| compile of loader, spark connector, hubble-be against the modified client | clean | |

What `DecimalPropertyApiTest` covers on the wire: property key `data_type: DECIMAL` create/get through the API and
`SchemaManager`; a vertex with `new BigDecimal("12345678901234567890.10")` read back as the same plain string via
the vertex API, the driver and Gremlin; `1E-18`, `42`, uint256 max and an integer literal stay exact; `"1,10"` is
rejected with 400; batch update with `SUM` gives `0.3` for `0.1 + 0.2` and keeps the 18th fraction digit on a
21-digit value; a range index on a decimal key is rejected with 400.

## Side findings

- `serializer/direct/struct/DataType` in the client could never be initialised: the static block that fills the
  code table ran before the table was created (NPE on first use). Nothing referenced it until the new unit test did;
  fixed by ordering the initialisers.
- Two `BatchUpdateElementApiTest` assertions expect the INTERSECTION error to end in `Date, String`; #3209 normalises
  batch values to the property's data type before the strategy runs, so the server now says `Date, Date`. The
  assertion was relaxed to the prefix so the client test passes on both servers.
- Against this server, `testBatchCreateWithMoreThanBatchSize` / `testEdgeBatchUpdateWithInvalidArgs` (expect 400
  above the batch limit) and `testCreateVertexWithTtlAndTtlStartTime` (timezone) fail regardless of this change; not
  investigated here.
- Batch update overwrites the vertex with the properties of the request (properties absent from the request are
  gone afterwards), on 1.7.0 as well; the driver test only asserts on the property it sent.
