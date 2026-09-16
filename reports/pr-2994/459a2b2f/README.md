# PR #2994 at head `459a2b2f` on master `1a15e762` (with #3184), 2026-09-16

Requested by the maintainer after #3184 merged: the HStore/RocksDB regression matrix for #2994 on a master that
already carries the sort-key guard. The PR head is not rebased yet, so the tree under test is a local merge:
`pr2994m` = `1a15e762` + `459a2b2f` (merge `a4c024d4`, no conflicts). Baseline `master1a15` = `1a15e762`.
Same lab as the earlier reports (PD + 3 stores, hstore server :8080, rocksdb oracle :8081), both server dists of a
tag built from the same tree (core jar `89ef9510` for master, `b7791473` for the merge), full wipe between sides.

## 1. Backend axis: hstore vs rocksdb on the same build

| build | suite (174 cases) | hg_j8 (207 cases) |
|---|---|---|
| `master1a15` | **OK=168, MISMATCH=0, TARGET-ERR=0**, BOTH-ERR=6 (4 UTF byte-order shapes in S, 2 negative-label shapes in J that error on both backends) | OK=182, MISMATCH=0, TARGET-ERR=0, BOTH-ERR=25 (the negative-label and paging shapes that #2994 fixes error on both backends) |
| `pr2994m` | **OK=170, MISMATCH=0, TARGET-ERR=0**, BOTH-ERR=4 (the UTF shapes only) | OK=190, **MISMATCH=6, TARGET-ERR=6**, BOTH-ERR=5; all twelve are the six `G PAGE` positive controls, see §3 |

The 37 store-side decode errors that section S produced on master before #3184 are gone on plain `1a15e762`:
`TARGET-ERR=0` on both suites. That is the merged guard, confirmed on a fresh cluster.

## 2. Version axis: what #2994 changes, same backend

| comparison | result |
|---|---|
| suite, rocksdb `master1a15` → `pr2994m` | OK=168, MISMATCH=0, ORACLE-ERR=2 (two `without()` shapes that error on master and pass with the PR), BOTH-ERR=4 |
| suite, hstore `master1a15` → `pr2994m` | identical: OK=168, MISMATCH=0, ORACLE-ERR=2, BOTH-ERR=4 |
| hg_j8, rocksdb `master1a15` → `pr2994m` | OK=121, **MISMATCH=60**, ORACLE-ERR=21, TARGET-ERR=1, BOTH-ERR=4 |
| hg_j8, hstore `master1a15` → `pr2994m` | OK=109, **MISMATCH=66**, ORACLE-ERR=21, TARGET-ERR=7, BOTH-ERR=4 |

The 60 mismatches are the same on both backends and are the point of the PR: `g.V().has('score',gte(40)).<barrier>.hasLabel(neq('person'))` and its `not()/where()/union()` variants return 0 on master and the 60 (or 48 with `age>=30`) robots with the PR; `or(neq(person), cnt=5)` grows from 180 to 240. The 21 ORACLE-ERR are shapes that error on master and pass with the PR. The one rocksdb TARGET-ERR is `hasKey('nope').hasLabel(neq('person'))`, which master answers empty and the PR turns into the documented capacity fallback error. On hstore the six extra mismatches and target errors are §3.

## 3. New on this head: paged range-index queries from Gremlin fail on HStore with a sandbox error

Six `G PAGE` positive controls (`score>=40` without a label and `robot age>=60`, page sizes 7 / 50 / 500) fail on
hstore with the PR and pass on rocksdb with the PR, on master hstore warm (`j8_master1a15_hstore.txt`) and on master
hstore fresh with J8 as the very first traffic (`j8_master1a15_hstore_fresh.txt`, 0 sandbox warnings in the log).
A second run on the warm PR server gives the same six (`j8_pr2994m_hstore_warm.txt`), so it is not a cold-start
artefact. The server answers `500 Not allowed to access thread group via Gremlin`; the stack
(`sandbox-thread-group-stack.txt`):

```
HugeSecurityManager.checkAccess(HugeSecurityManager.java:169)
java.lang.Thread.<init>
org.apache.hugegraph.store.client.util.ExecutorPool$DefaultThreadFactory.newThread(ExecutorPool.java:64)
java.util.concurrent.ThreadPoolExecutor.execute / ExecutorCompletionService.submit
org.apache.hugegraph.store.client.OrderedKvIterator.initialize(OrderedKvIterator.java:200)
org.apache.hugegraph.store.client.OrderedKvIterator.hasNext(OrderedKvIterator.java:93)
HstoreSessionsImpl$ColumnIterator.<init>(HstoreSessionsImpl.java:245)
HstoreSessionsImpl$HstoreSession.scanOrdered(HstoreSessionsImpl.java:745)
HstoreTable.queryByRange(HstoreTable.java:643) <- queryBy <- query <- HstoreStore.query
```

So with the PR these paged range-index queries reach `HstoreSession.scanOrdered()`, whose `OrderedKvIterator`
creates the worker threads of the store client's `ExecutorPool` lazily, on first use, and
`HugeSecurityManager.checkAccess(ThreadGroup)` forbids thread creation from a Gremlin thread. The whitelist in that
method (`callFromCaffeine`, `callFromAsyncTasks`, `callFromEventHubNotify`, `callFromBackendHbase`, `callFromRaft`,
`callFromSofaRpc`) has no entry for `org.apache.hugegraph.store.client`. On master the same six shapes never take the
ordered scan from a Gremlin thread, so the pool is never created under the sandbox. Two possible fixes, both outside
the PR's own diff: start the `ExecutorPool` eagerly when the store client is created, or add the store client
package to the sandbox whitelist the way raft and sofa-rpc are. Until then, `hasLabel(neq(...))`-free paging over a
range index via Gremlin fails on HStore with #2994 while it works on master.

## Files

`{master1a15,pr2994m}_{oracle,hstore}.{json,txt}`, `compare_*.txt` (backend axis),
`compare_version-axis_*_master1a15_vs_pr2994m.txt`, `j8_*` (hg_j8 runs and comparisons, incl. `_warm` and `_fresh`),
`sandbox-thread-group-stack.txt`.
