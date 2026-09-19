# #3164 (snapshot save vs compaction, #3162) on pods, 2026-09-19

Harness `cluster/k3s_3164.py`: POST `/v1/compat?id=P` on the Store REST, `/test/snapshot` `gap` ms later, SIGKILL the Store JVM in the container, kubelet restart, then check the partition's snapshot dirs (`__raft_snapshot_meta` + `data/`), `PState_Normal` and the log. The race counts as fired only if the #3164 line `snapshot save failed: compaction in progress` (EBUSY) is in the pre-kill log.

| run | partition | gap ms | verdict | race fired | Ready again s |
|---|---|---|---|---|---|
| run1 | 0 | 300 | INCONCLUSIVE | False | 17 |
| run1 | 3 | 300 | INCONCLUSIVE | False | 23 |
| run1 | 6 | 300 | INCONCLUSIVE | False | 36 |
| run2 | 1 | 0 | INCONCLUSIVE | False | 61 |
| run2 | 4 | 50 | INCONCLUSIVE | False | 102 |
| run2 | 7 | 150 | INCONCLUSIVE | False | 175 |
| run3 | 10 | 500 | INCONCLUSIVE | False | 313 |
| run3 | 5 | 300 | INCONCLUSIVE | False | 314 |
| run3 | 8 | 100 | INCONCLUSIVE | False | 325 |
| run4 | 0 | 600 | INCONCLUSIVE | False | 324 |
| run4 | 3 | 50 | INCONCLUSIVE | False | 334 |
| run5 | 0 | 500 | INCONCLUSIVE | False | 13 |
| run5 | 3 | 1500 | INCONCLUSIVE | False | 23 |

Data size per run: run1-3 ~20 MB partitions (the fault-battery oracle), run4 after 1 M padding vertices (partitions 0 and 3 ~100 MB), run5 after 5 M (partition 3 ~350 MB, 0 ~107 MB). No attempt reached the window: `/test/snapshot` only queues the jraft snapshot and `compactRange()` of a 350 MB partition releases its lock within one log second on these SSD-backed nodes, so every snapshot was written complete (`data=10 files`). Every restart came back `PState_Normal` with no `snapshot is corrupt` line. The 300+ s `Ready again` values in run4 are kubelet's CrashLoopBackOff after repeated exit-137 kills of the same container, not Store startup time. The fix itself is in the image: `hg-store-core` in `hugegraph/store@sha256:879ee2d1` contains `tryLockCompactionRange`, the `compaction in progress` EBUSY message and the `snapshot is corrupt` load check.
