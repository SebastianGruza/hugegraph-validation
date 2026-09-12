# Write cost of edge indexes on HStore (1 M edges, 3 store nodes), 2026-09-12

Question: how much do a low-cardinality **label index** on an edge label plus a **SECONDARY index on a string
property** (`addr`, also the first sort key) cost at write time, compared with the same edge label carrying no index
at all? Everything else identical.

## Setup

- Lab of [docs/setup.md](../../docs/setup.md): PD + 3 store nodes (one per VM, `default-shard-count: 1`, 12 partitions),
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
| store data on disk after load + 60 s (sum of 3 nodes, `du`) | 736 MB / 736 MB | 1 081 MB / 1 081 MB | **1.47** |
| store CPU (sum of 3 processes, load + 60 s) | 93 s / 89 s | 125 s / 143 s | 1.34–1.61 |
| server CPU (load + 60 s) | 50 s / 50 s | 67 s / 70 s | 1.34–1.40 |
| store RSS growth during the run (sum of 3) | 1 255 MB / 1 228 MB | 1 617 MB / 1 640 MB | 1.29–1.34 |
| server RSS growth | 152 MB / 397 MB | 121 MB / 197 MB | noisy, no signal |
| average node CPU busy | 6 % | 8 % | 1.33 |
| errors | 0 | 0 | |

Raw: `r1/`, `r2/` (`<variant>.json` = client-side result, `<variant>_<host>.csv` = sampler), `run.log`.

## Reading

- The two indexes cost **30 % of write throughput and 45 % more disk** for the same 1 M edges, with **34–61 %
  more store CPU** and **~35 % more server CPU**. The label index alone is one extra row per edge, the `addr` index
  another; both are written in the same raft transaction as the edge, which is where the latency (p50 +45 %) comes
  from — every batch now carries three tables instead of one.
- The cluster was nowhere near saturated (6–8 % busy): the cost shows up as latency per batch, i.e. as throughput
  per client thread. More writer threads would hide part of it until CPU or raft becomes the limit.
- `du` at this size is dominated by preallocated RocksDB WAL files (the sampler's start-to-end delta is 12 MB vs
  356 MB, while the final directories are 736 MB vs 1 081 MB); every node still has exactly 12 SST files after the
  run, i.e. the data has not been through a real compaction cycle. **The compaction cost of the indexes is therefore
  not in these numbers** — it appears at 10–100× this size, when the index tables have their own LSM levels to
  rewrite. Treat the 30 % / 45 % as a lower bound.
- For the workload these labs are about (every traversal starts from a known vertex), neither index is ever used
  by the planner (see the planner notes in the PR #2994 reports: `OWNER_VERTEX` queries go by edge key and sort keys
  only), so this is pure cost.

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
```
