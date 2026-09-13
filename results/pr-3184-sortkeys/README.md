# PR #3184 — sort-key query shapes on HStore, master vs PR head, with a RocksDB oracle (2026-09-13)

Lab of [docs/setup.md](../../docs/setup.md): PD + 3 stores, hstore server on :8080 and a rocksdb oracle server on
:8081 built from the same tree. Data: the sk4 schema of `cluster/idx_bench.py` (edge label `xfer` with four sort keys
`[btc_addr, seq, height, asset]`, vertex ids = BTC/ETH-style addresses), **5 000 000 edges**, generator made
deterministic per batch so both backends hold the same edges. `cluster/sk_shapes.py <port> <label> <vertex>` runs 13
shapes from one fixed vertex and returns sorted edge-id lists; `cluster/sk_shapes_ab.sh` is the whole cycle.

Sides: master `36811483` dists (`hugegraph-hstore` `e0083805`), PR #3184 head `0ecc10a3` dists (`a935012f`), the
servers swapped in place without reloading the data.

| shape (from `g.V(v)`) | master hstore | master rocksdb | #3184 hstore | #3184 rocksdb | id sets #3184 hstore = rocksdb |
|---|---|---|---|---|---|
| `outE('xfer')` (no sort-key condition) | 57 | 57 | 57 | 57 | same |
| `.has('btc_addr',A)` prefix 1 | **500 `Can't construct Cardinality from code 0`** | 2 | 2 | 2 | same |
| `.has('btc_addr',A).has('seq',S)` prefix 2 | **500** | 1 | 1 | 1 | same |
| prefix 1 + `has('seq',gte(1))` | **500** | 2 | 2 | 2 | same |
| prefix 2 + `has('height',between(..))` | **500** | 1 | 1 | 1 | same |
| all four keys `eq` | 1 | 1 | 1 | 1 | same |
| prefix 3 + `has('asset',gte('a'))` | 1 | 1 | 1 | 1 | same |
| `.has('seq',S)` alone (not a prefix, filter) | 1 | 1 | 1 | 1 | same |
| `.has('amount',gte(0))` (not a sort key) | 57 | 57 | 57 | 57 | same |
| prefix 1 + `has('amount',gte(0))` | **500** | 2 | 2 | 2 | same |
| `bothE('xfer').has('btc_addr',A)` | **500** | 2 | 2 | 2 | same |
| `...inV().inE('xfer').has('btc_addr',A)` | **500** | 1 | 1 | 1 | same |
| prefix 1 + `valueMap()` | **500** | 2 | 2 | 2 | same |

On master with HStore, **every partial sort-key prefix and every range on a non-last sort key fails** (8 of 13
shapes); only "no condition", "all keys equal" and non-prefix filters work. With #3184 all 13 pass and return the
same edge ids as RocksDB, on the same data, from the same vertex.

Raw: `master.jsonl`, `pr3184.jsonl` (one JSON line per shape with the id list or the error), `run_all.log`.
