# Cycle 4 (2026-09-21): graph-id regression on the in-place upgrade 1.7.0 -> master, both triggers, rollback, workaround

Same lab as cycle 3 (PD + 3 Stores on .235/.236/.237, 12 partitions, Server on .235, official 1.7.0 binaries,
`usePD=true`), master jars now built from `83ef9f3f` (includes #3220, so the schema is visible after the swap and
every query of `hg_data.py read` is meaningful; 16 Gremlin checks compared with the 1.7.0 baseline).
Harness: `cycle4_graphid.sh A|B` (from the workstation), `deploy_m3220.sh` (jars), logs and reads under `A/` and `B/`.

Data on 1.7.0: batch load 29 V / 63 E, forced raft snapshot on every partition, second batch of 5 vertices: 34 V / 63 E.

| step | A: batch entries left in the raft log at the swap | B: snapshot again right before the stop |
|---|---|---|
| master start, no client write | **15 V / 23 E**, 8 allocations per store at startup (raft replay), 0 `Failed to parse entry` | 30 V / 56 E, 2 allocations per store (the second snapshot did not cover every partition's tail) |
| first client batch on master (1 vertex) | n/a | **23 V / 41 E**, 4 allocations per store |
| identical checks vs 1.7.0 baseline | 0/16 | 4/16 |
| `/fix/graph_ids/{pid}` | 1.7.0 rows under `65534`, `"graph": "not found"` | same |
| rollback: 1.7.0 jars on the allocated mapping | server up but graph not exposed within the wait (not measured) | **23 V / 41 E**: 1.7.0 honours the mapping, rollback does not restore the view |
| workaround: `POST :8520/fix/update_graph_id/{pid}` `{"DEFAULT/hugegraph/g": 65534}` on all 36 partition replicas | **34 V / 63 E, 16/16 identical** | **34 V / 63 E, 16/16 identical** |
| write under the sentinel mapping, then store restart | 35 V, 0 new allocations, 14/16 (the extra vertex) | 36 V (the extra vertex plus the replayed master batch), 0 new allocations |

Reading: the visible set after the upgrade is whatever the raft log replayed (or the first client batch wrote) under the
newly allocated id; everything only in snapshots stays under `0xFFFE` and is unreachable from the server. With the
schema intact (#3220) there are no parse errors: the rows are simply not read. Rolling the jars back to 1.7.0 does not
help. Mapping the graph to `0xFFFE` on every partition of every store brings the whole 1.7.0 data set back exactly
and survives restarts; rows written under the allocated id in between reappear only if their raft entries are
replayed after the remap (variant B: `c400`).
