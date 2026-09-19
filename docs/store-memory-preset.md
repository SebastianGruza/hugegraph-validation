# Store OOM under the chart's cluster preset (found 2026-09-19 while loading data for the #3164 run)

While loading 5 M padding vertices (1 KB text each, `cluster/k3s_pad_load.py`) into the 3-node k3s cluster running
the #3218/#221 chart with `values-cluster.yaml`, all three Stores were OOM-killed by the kernel and then stayed in
`CrashLoopBackOff`, dying within ~16 s of every restart. Evidence in `results/store-oom-cluster-preset/`
(`dmesg-*.txt`, `describe-store-*.txt`, `events.txt`, `top.txt`, `helm-values.yaml`, previous-container logs,
the loader log with 1184 failed batches from vertex 2.36 M on).

| item | value |
|---|---|
| preset | `store.javaOpts: -Xms512m -Xmx1024m -XX:MaxMetaspaceSize=256m -XX:MaxDirectMemorySize=512m`, `store.resources.limits.memory: 4Gi`, requests 2Gi |
| kernel | `Memory cgroup out of memory: Killed process (java) anon-rss:4154896kB` on all three nodes, first at 08:29 (store-2), 08:31 (store-1), 08:35 (store-0), i.e. during the load once ~3.4 GB had been written |
| data at the time | ~1.0-1.3 GB RocksDB + 4.2 GB raft log per Store (12 partitions, replica 3) |
| after raising the limit to 8Gi | Stores start and stay up; `kubectl top` 7.2-7.4 GiB for store-1/2, 5.4 GiB store-0; `/proc/<java>/status` on store-1: VmRSS 7.45 GiB = **RssAnon 4.42 GiB** + RssFile 3.03 GiB (page cache of the raft log and SSTs); cgroup `memory.current` 7.82 GB of 8.59 GB |

Where the 4.4 GiB of anonymous memory comes from, with a 1 GiB heap:

- heap ≤ 1 GiB (`-Xmx1024m`) and direct ≤ 0.5 GiB (`MaxDirectMemorySize=512m`);
- the data RocksDB budget `rocksdb.total_memory_size` is **not set by the chart**, and `AppConfig.init()` defaults it to
  `Runtime.maxMemory()`, i.e. another 1 GiB (66 % write buffers, 34 % block cache) that fills as data arrives;
- `RaftRocksdbOptions.registerRaftRocksdbConfig()` creates a separate `LRUCache(1 GiB)` for the jraft `RocksDBLogStorage`
  (once per process, shared by the groups) plus 12 groups × 3 × 8 MB raft memtables, uncompressed raft logs;
- 1085 threads (600 `hg-grpc`, 128 `hg-scan`, the rest jraft/Bolt), metaspace 87 MB, glibc arenas.

Sum ≈ 4.2-4.6 GiB, matching the measured 4.42 GiB. The 4Gi limit of the cluster preset is therefore below what the
image's own defaults commit at steady state with a 1 GiB heap, independent of data size beyond ~1 GB: the Store is
fine while its caches are cold (the fault battery of 2026-09-16 ran with 0.2 GB of data) and dies once they warm.

Suggested chart change (for #221): size `store.resources.limits.memory` from the JVM flags as
`Xmx + MaxDirectMemorySize + rocksdb.total_memory_size + 1 GiB (raft log cache) + ~0.5 GiB (threads, metaspace)`, and pass
`rocksdb.total_memory_size` explicitly (it is a `rocksdb:` map in `application.yml`, so the entrypoint's
`SPRING_APPLICATION_JSON` could carry it from a `store.rocksdb.totalMemorySize` value) instead of letting it silently
equal the heap. With the current flags that is ≥ 4.5 GiB, so either 6Gi limits or a smaller RocksDB budget. Also worth
a line in the README: the raft log (`raft.max-log-file-size: 600000000000`, no compression, `snapshotInterval: 1800`)
was 4× the data on disk here and is what fills the page cache.

Side observation, unrelated to memory: every Store logs `[ERROR] IpUtil - getRaftAddress, got exception, For input
string: "hugegraph-store-0"` twice per heartbeat cycle at startup (the raft address is a DNS name, the parser expects
an IP); harmless but noisy.

Method note for the harness: killing the Store JVM inside the container (`kill -9 <pid>`) repeatedly makes kubelet
back off restarts (`CrashLoopBackOff`, up to 5 min) since each exit is code 137; for repeated crash tests delete the pod
(`--force --grace-period=0`) instead, as the fault battery does.
