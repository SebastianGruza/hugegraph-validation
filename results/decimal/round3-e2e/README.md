# DECIMAL (#3209) round 3: server VertexApiTest on rocksdb and hstore (2026-09-30)

Server branch `feat/decimal-datatype` at the round-3 head (exact fractions only in `properties`, BigDecimal typed in
the store query wire format, GraphSON plain form within the scale bound, direct scale check). Built on the lab from
that head; rocksdb server on .235:8081, hstore server on .235:8086 (graph store `decb` on the lab PD), and the three
Stores restarted with the store jar from the same build.

| backend | `VertexApiTest` (api-test profile) |
|---|---|
| rocksdb | 6/6 (`vertexapi-rocksdb.txt`) |
| hstore | 6/6 (`vertexapi-hstore.txt`) |

`testDecimalJsonNumberLiteralIsExact` now covers: a 39-digit JSON number literal on a DECIMAL key (exact) and on a
DOUBLE key (narrowed), an exponent literal, an edge query by vertex + label + `properties={"amount": ...}` that hits
for the exact 39-digit value and misses for the same value with the last digit changed (on hstore the condition is
pushed down to the Store, which is where the untyped BigDecimal used to become a double), a DOUBLE key with a
fractional `~default_value` echoed as a JSON number, and an INT key still rejecting a fraction.
