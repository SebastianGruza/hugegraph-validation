# apache/hugegraph#3162 / PR #3164 — compaction vs raft snapshot race, reproduced and measured (2026-09-12)

Lab of [docs/setup.md](../../docs/setup.md), replication 1 (every partition a single-replica raft group on its store).
Data: 25 M `xfer` edges (`cluster/idx_bench.py sk4-noindex`, 5 M loaded and compacted, then 20 M appended so the
partitions carry unflushed data), partitions of 60–275 MB on the store at 192.168.80.235.

Scripts: [`cluster/race_snapshot_compaction.sh`](../../cluster/race_snapshot_compaction.sh) (blocking full compaction
of one partition through the store REST `POST :8520/v1/compat?id=`, then `GET :8520/test/snapshot` a fixed number of
ms later, then the partition's raft snapshot directory), `..._kill.sh` (same, then `SIGKILL` the store while the
compaction is still running, restart without wiping, report). Logs: `store-3162.log.gz`, `raft-3162.log.gz`.

| step | master `60c8803` | PR #3164 head `1c6bc23` |
|---|---|---|
| already-compacted 62 MB partition, snapshot 100 ms after the compaction request | compaction takes 0.1 s, snapshot after it: valid (window too narrow) | |
| 62 MB + ~250 MB unflushed, snapshot 300 ms after | compaction 7 s; `onSnapshotSave success` logged **during** it; meta-only snapshot committed, superseded 7 s later by the post-compaction snapshot | |
| 274 MB partition, snapshot 300 ms after, `SIGKILL` at +1.3 s | `snapshot_50210/`: `__raft_snapshot_meta` present, no `data/`, no `should_not_load`; jraft had already **deleted the previous good snapshot** (`snapshot_50208`) and truncated the log prefix to 50209 | |
| restart, same on-disk state | `onSnapshotLoad failed` → `initSnapshotStorage failed` → `Raft 15 is restarting !!!` once, then nothing; partition 15 stays down | same outcome; only the message changes to `snapshot is corrupt, data dir ... ` |
| what clients see meanwhile | `g.E().count()` = 24 045 646 of 25 000 000, `g.V().count()` = 97 185 of 100 000, **HTTP 200, no error** on the server; store REST `/v1/partition/15` → 500 | same |
| `rm -rf snapshot_50210`, restart | `Missing logs in (0, 50209)` → still down (the log prefix is gone) | same |
| `rm -rf raft/00015` (log + meta + snapshot), keep `db/00015`, restart | partition back (`V` = 100 000) but `E` = 24 742 999: **257 001 edges lost** — HStore runs RocksDB with `setDisableWAL(true)` (raft log is the WAL), so the unflushed memtable of the killed process is gone with the raft log | |
| same race on the PR jar (236 MB partition, 300 ms) | | `Raft 3 onSnapshotSave failed ... snapshot save failed: compaction in progress`, `EBUSY`, **no meta-only directory**, compaction finishes in 1 s, blank-task snapshot after it is valid |

Timings for the 10 s lock-wait discussion in the PR: `onSnapshotSave` on a 274 MB partition completes within the same
log second (checkpoint = hard links, CRC64 over the first/last 4 KB of each file); `compactRange` of a partition with
~250 MB of unflushed data takes 7 s, of an already compacted 62 MB partition 0.1 s.
