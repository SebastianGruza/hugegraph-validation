# Write cost of edge indexes on HStore (1 M / 20 M edges, four sort keys, replication 1 and 3), 2026-09-12

> **Erratum (2026-09-12, same day).** The 1 M table below reports "store data on disk" as `du` of the store's whole
> `storage` directory. That directory holds three things: the RocksDB data (`db/`), the raft log (`raft/*/log`, a full
> copy of every write until the next snapshot) and the raft snapshots (`raft/*/snapshot`, a copy of the SST files).
> At 1 M edges nothing had been flushed from the memtables yet, so the "+45 % disk" was mostly raft log, not data.
> The 20 M section measures the three separately after a forced flush + compaction of every partition; the real
> data-on-disk cost of the two indexes is **+69 %**. Also: the lab has **36 partitions (12 per store)**, not 12 —
> `initial-store-count × store-max-shard-count / default-shard-count` = 3 × 12 / 1.

Question: how much do a low-cardinality **label index** on an edge label plus a **SECONDARY index on a string
property** (`addr`, also the first sort key) cost at write time, compared with the same edge label carrying no index
at all? Everything else identical.

## Setup

- Lab of [docs/setup.md](../../docs/setup.md): PD + 3 store nodes (one per VM, `default-shard-count: 1`, 36 partitions, 12 per store),
  hugegraph-server master `60c8803` (hstore dist) on the PD node. Fresh cluster (PD, stores and server wiped) before
  every run.
- Schema: `acct` vertices (`CUSTOMIZE_STRING`), edge label `flow(acct → acct)`, `frequency=MULTIPLE`,
  `sort_keys=[addr, ts]`, properties `addr TEXT`, `ts LONG`, `amount DOUBLE`.
  - **index**: `enable_label_index=true` on `flow` + `flowByAddr = SECONDARY(addr)` on `flow`.
  - **noindex**: `enable_label_index=false`, no index label.
- Data: 100 000 vertices, then **1 000 000 edges** between random vertex pairs, `addr` = 20-hex-char address of the
  target (~100 k distinct values), `ts` increasing, `amount` random. `POST /graph/edges/batch?check_vertex=false`,
  batches of 500, **4 writer threads**, `cluster/idx_bench.py`.
- Sampling (`cluster/sample_res.sh`, every 5 s on each node): store and server RSS, process CPU ticks, `du` of the
  store's `storage` dir, system idle ticks. Summary by `cluster/idx_summarize.py`. Two repetitions of each variant.

## Results

| | no index (r1 / r2) | label + addr index (r1 / r2) | ratio |
|---|---|---|---|
| throughput, edges/s | 54 484 / 56 382 | 38 100 / 38 848 | **0.69–0.70** |
| 1 M edges wall time | 18.4 s / 17.7 s | 26.2 s / 25.7 s | 1.42–1.45 |
| batch latency p50 | 32 / 31 ms | 47 / 45 ms | 1.45 |
| batch latency p99 | 90 / 73 ms | 107 / 115 ms | 1.2–1.6 |
| slowest batch | 235 / 226 ms | 358 / 313 ms | 1.4–1.5 |
| `du` of the store `storage` dir after load + 60 s (sum of 3 nodes; **mostly raft log, see erratum**) | 736 MB / 736 MB | 1 081 MB / 1 081 MB | 1.47 |
| store CPU (sum of 3 processes, load + 60 s) | 93 s / 89 s | 125 s / 143 s | 1.34–1.61 |
| server CPU (load + 60 s) | 50 s / 50 s | 67 s / 70 s | 1.34–1.40 |
| store RSS growth during the run (sum of 3) | 1 255 MB / 1 228 MB | 1 617 MB / 1 640 MB | 1.29–1.34 |
| server RSS growth | 152 MB / 397 MB | 121 MB / 197 MB | noisy, no signal |
| average node CPU busy | 6 % | 8 % | 1.33 |
| errors | 0 | 0 | |

Raw: `r1/`, `r2/` (`<variant>.json` = client-side result, `<variant>_<host>.csv` = sampler), `run.log`.

## Reading

- The two indexes cost **30 % of write throughput** (and, per the 20 M section, **69 % more data on disk**) for the same 1 M edges, with **34–61 %
  more store CPU** and **~35 % more server CPU**. The label index alone is one extra row per edge, the `addr` index
  another; both are written in the same raft transaction as the edge, which is where the latency (p50 +45 %) comes
  from — every batch now carries three tables instead of one.
- The cluster was nowhere near saturated (6–8 % busy): the cost shows up as latency per batch, i.e. as throughput
  per client thread. More writer threads would hide part of it until CPU or raft becomes the limit.
- `du` at this size is dominated by the raft log and preallocated WAL files (see the erratum); every node still has
  exactly 12 SST files after the run, i.e. nothing has been flushed. The 20 M section below has the real numbers.
- For the workload these labs are about (every traversal starts from a known vertex), neither index is ever used
  by the planner (see the planner notes in the PR #2994 reports: `OWNER_VERTEX` queries go by edge key and sort keys
  only), so this is pure cost.

## 20 M edges — data, raft log and snapshots measured separately

Same schema and generator, 20 000 000 edges, one run per variant. New procedure (`cluster/idx_run_variant.sh`):
after the load, 60 s settle, then every partition is flushed + compacted through the store REST
(`POST :8520/v1/compat?id=<partition>`, one at a time — the store allows a single manual compaction), then
`cluster/idx_disk_split.sh` records `db/`, `raft/*/log`, `raft/*/snapshot` and the per-table key counts and sizes
that the store reports (`GET :8520/v1/partitions`). The no-index run's per-table row is from one node only (the
script was extended between the runs); its key counts are exactly one third of the index run's edge rows.

| | no index | label + addr index | ratio |
|---|---|---|---|
| throughput, edges/s | 69 443 | 47 576 | **0.69** |
| 20 M edges wall time | 288 s | 420 s | 1.46 |
| batch latency p50 / p99 | 26 / 53 ms | 38 / 85 ms | 1.46 / 1.60 |
| **data on disk, `db/` after flush + compaction (3 nodes)** | **3 230 MB** | **5 471 MB** | **1.69** |
| RocksDB keys (3 nodes) | 40.2 M | 80.4 M | 2.00 |
| `g+oe` / `g+ie` (edge rows) | 1 725 / 1 329 MB (20.0 M + 20.0 M keys) | 1 716 / 1 320 MB | 1.00 |
| `g+index` | 3 MB (vertex label index only) | **2 224 MB, 40.1 M keys** | |
| raft log (3 nodes) | 8 089 MB | 7 794 MB | copy of every write until the next snapshot |
| raft snapshot (3 nodes) | 3 081 MB | 5 323 MB | RocksDB checkpoint = **hard links** to the `db/` SSTs (link count 2), so `du` counts them twice; the real extra space is only the SSTs compacted away since the snapshot |
| store CPU (sum of 3, load + settle + compaction) | 922 s | 1 968 s | 2.13 |
| server CPU | 370 s | 728 s | 1.97 |
| store RSS growth (sum of 3) | 6.2 GB | 17.2 GB | 2.75 |
| errors | 0 | 0 | |

So the two indexes are exactly two extra rows per edge (label index + `addr` index), and those rows are **as large
as the edge rows themselves** (2.2 GB for 40 M index rows vs 3.0 GB for 40 M edge rows): a SECONDARY index key
carries the indexed value plus the complete edge id (both vertex ids and every sort key). The throughput ratio is
the same as at 1 M (0.69), store CPU grows from 1.5× to 2.1×.

Raw: `20m/` (`noindex*`, `index*`: client json, samplers, `*_disk_<host>.txt`, run logs). A first 20 M index
run in `run_chain.log` (12:16–12:36 lab time) was discarded: it overlapped with a killed driver and ended with
39.7 M edges in the store.

## Four sort keys, hot addresses, replication 1 vs 3

`cluster/idx_bench.py sk4-noindex|sk4-index`, 5 000 000 edges, 100 000 vertices whose ids are BTC addresses
(base58, 34 chars, half of them) or ETH-like addresses (`0x` + 40 hex). Edge label `xfer` with **four sort keys**
`[btc_addr, seq, height, asset]`: `btc_addr` = the counterparty's address (the target's id when it is a BTC
address), `seq` random 1..1000, `height` a block height growing by one every 3 000 edges, `asset` = `btc` (70 %)
or one of 50 40-hex contract addresses. **Hot spot:** 50 % of all edges point at 100 hot BTC vertices (0.1 % of the
vertices), the other 50 % at uniformly random vertices. Index variant: label index + `SECONDARY(btc_addr)`.
Replication 3 = PD `partition.default-shard-count: 3` on a wiped cluster, which gives 12 partitions × 3 replicas
(`3 × 12 / 3`), every partition on every store, instead of 36 × 1.

| | rep 1, no index | rep 1, label + btc_addr index | rep 3, no index | rep 3, index |
|---|---|---|---|---|
| throughput, edges/s | 47 281 | 37 330 (0.79) | 31 909 (**0.67**) | 26 296 (0.56) |
| batch latency p50 / p99 | 36 / 85 ms | 47 / 108 ms | 55 / 130 ms | 67 / 156 ms |
| data on disk, one copy (`db/`, compacted) | 1 816 MB | 3 360 MB (**1.85**) | 1 714 MB | 3 207 MB |
| data on disk, whole cluster | 1 816 MB | 3 360 MB | 5 142 MB | 9 621 MB |
| `g+oe` / `g+ie` | 864 / 754 MB | 876 / 764 MB | | |
| `g+index` | 6 MB | **1 532 MB** (10.1 M keys) | | 1 491 MB |
| raft log (whole cluster) | 2 620 MB | 4 454 MB | 7 053 MB | 12 426 MB |
| store CPU (sum of 3) | 426 s | 610 s | 972 s | 1 415 s |
| server CPU | 154 s | 261 s | 182 s | 296 s |
| errors | 0 | 0 | 0 | 0 |

- **Per edge**: 363 B of edge data with four sort keys and 34–42-char vertex ids, vs 162 B with two sort keys and
  7-char ids; the `btc_addr` index adds **309 B per edge** vs 112 B for `addr` in the 2-sort-key schema. Longer
  edge ids make every index row longer, so the index is bigger than the edges it indexes (1.5 GB vs 1.6 GB).
- **Where the hot spot lands** (rep 3, `sk4-r3/sk4-index_partition_tables_235.jsonl`, all 12 partitions on one
  node): `g+oe` is flat (410–426 k keys per partition), `g+ie` ranges **280–533 k (1.9×)** because the in-edges of a
  hot vertex all live in its owner slot, `g+index` ranges 736–983 k (1.3×) because the 100 hot values hash to ~100
  different slots. The imbalance is in the edge table owned by the hot vertices, not in the index.
- **Replication 3** costs 0.67× throughput and 2.3× store CPU by itself (three RocksDB instances and three raft logs
  per write); with the indexes on top, 0.56× of the unreplicated no-index baseline.
- The store REST `GET /v1/partitions` returns HTTP 500 on a replicated cluster; `GET /v1/partition/{id}` works, and
  `idx_disk_split.sh` falls back to it.

Raw: `sk4-r1/`, `sk4-r3/`, `run_chain.log`.

## Reproduce

```bash
# on the workstation, with the lab of docs/setup.md
for V in noindex index; do
  # wipe + boot the cluster (see cluster/run_side.sh for the sequence), then on every node:
  #   setsid nohup bash ~/sample_res.sh ~/idxbench/${V}_$(hostname).csv 5 &
  ssh $SERVER "python3 -u ~/idx_bench.py $V 1000000 4 500 100000 ~/idxbench/$V.json"
  # stop the samplers after 60 s, collect ~/idxbench/*, then:
done
python3 cluster/idx_summarize.py <dir with noindex.json, index.json and the csv files>

# 20 M and sk4 runs, new procedure (flush + compaction, disk split, per-table sizes):
cluster/idx_run_variant.sh 20m noindex 20000000; cluster/idx_run_variant.sh 20m index 20000000
cluster/idx_run_chain.sh          # 20 M index, then sk4-noindex/sk4-index at replication 1 and 3
python3 cluster/idx_summarize2.py "no index=results/index-cost/20m/noindex" "index=results/index-cost/20m/index"
```
