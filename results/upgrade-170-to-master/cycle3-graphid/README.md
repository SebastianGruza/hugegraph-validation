# Cycle 3 (2026-09-21): 1.7.0 batch data under graph id 0xFFFE is lost after the in-place upgrade to master

Clean reproduction of the HStore graph-id regression (issue to follow; related: apache/hugegraph#3095, #3153, #3223).
Script: `cycle3_snapshot_upgrade.sh` (run from the workstation), log: `cycle3.log`, reads: `*.json` (from `hg_data.py`).

Lab: PD + 3 Stores (192.168.80.235/.236/.237, 12 partitions) + Server on .235, official 1.7.0 binaries, `usePD=true`.
Master jars = `4f5202b8` (2026-09-18; note: predates #3220, so schema-dependent queries hit #3219 after the swap and
only `g.V().count()` / `g.E().count()` are meaningful on the master side).

| step | server | `g.V().count()` | `g.E().count()` | note |
|---|---|---|---|---|
| A: batch load (`/graph/vertices/batch`, `/graph/edges/batch`) | 1.7.0 | 29 | 63 | rows written under `0xFFFE` (batch path used `getKey()`) |
| forced raft snapshot on every partition of every store (`GET :8520/test/snapshot`) | 1.7.0 | | | 12 `snapshot_*` dirs per store |
| B: second batch, 5 `cust` vertices | 1.7.0 | 34 | 63 | |
| stop, swap `lib/` to master on PD, stores, server, start, **no client write** | master | **18** | **23** | stores allocate real graph ids while replaying the raft log at startup (`GraphIdManager.getGraphIdOrCreate` from `PartitionStateMachine.onApply → doBatch`, 8 partitions per store); `/fix/graph_ids/{pid}` shows the 1.7.0 rows under `65534` as `"graph": "not found"`; server log: 6 × `Failed to parse entry … BufferUnderflowException` |
| workaround: `POST :8520/fix/update_graph_id/{pid}` `{"DEFAULT/hugegraph/g": 65534}` on all 12 partitions of all 3 stores | master | **34** | **63** | everything back |
| store restart with the sentinel mapping | master | 34 | 63 | mapping persisted (`has_slot_id: true`), no new allocation |

What decides whether an upgrade hits this: (1) the graph's first write per partition on 1.7.0 was a batch (loader, batch
REST), so its rows sit under `0xFFFE`; (2) any batch entry replayed from the raft log at the first master start, or the
first batch write after the upgrade, allocates a real id; from then on only rows replayed from the log are visible and
everything captured in snapshots is unreachable. Rolling the jars back to 1.7.0 does not help: the allocated mapping is
honoured by 1.7.0 as well.

The write attempt under the sentinel mapping in the log failed with `Undefined vertex label`: that is #3219 (master
jars without #3220), not the mapping; the earlier experiment on 2026-09-21 with the current master server wrote
`c300` under the sentinel successfully (`remap_test.sh`, see the session notes in the issue).
