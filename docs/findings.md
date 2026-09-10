# Findings

Status legend: **unreported** = only in this repository; **PR open** = submitted upstream;
**known** = acknowledged in upstream code or issues. Line numbers refer to apache/hugegraph
master `98477f0` unless stated otherwise.

## F1

**Page cursor re-emits the boundary record when the page limit is a multiple of 500**

Status: **reported — apache/hugegraph#3191** (2026-09-03); fixed locally, red/green validated — `patches/0001-fix-core-paging-*.patch`; PR to follow.

### Symptom
Following the `page` token with `limit=500` or `limit=1000`, the last element of page *k* is
returned again as the first element of page *k+1* — one duplicate per boundary. With
`limit` = 100 / 250 / 333 / 400 / 600 there is no duplicate. Identical on `rocksdb` and `hstore`,
so it is server code, not a backend.

Affected: any query whose result is one backend entry with more than 500 columns —
edges of one owner vertex (no condition, sort-key prefix, sort-key range, IN direction) and
vertex queries by label through the label index. Not affected: secondary (range/secondary/search)
index queries — each index key is its own entry — and full scans without a label.

### Evidence
`results/probe_*`, section S `PAGED asset=ETC, page size 500` (`n=1214 uniq=1212`), and the
matrix from `suite/page_probe.py` before the fix:

```
prefix asset=ETC   size=400  n=1212 uniq=1212 dups=0
prefix asset=ETC   size=500  n=1214 uniq=1212 dups=2 last==first@[0,1]
prefix asset=ETC   size=600  n=1212 uniq=1212 dups=0
prefix asset=ETC   size=1000 n=1213 uniq=1212 dups=1 last==first@[0]
V label=person     size=500  n=3006 uniq=3000   (rocksdb: at boundaries; hstore: 6 dups, not at boundaries)
V person age>=30   size=500  n=2332 uniq=2332   (range index: clean)
V no label         size=500  n=3505 uniq=3505   (full scan: clean)
```

HugeGraph's own core test, added by the patch, on `rocksdb`:
`testQueryOutEdgesOfVertexInPagingAtBatchBoundary: limit 500 expected:<1200> but was:<1202>`
before the fix; passes after.

### Root cause
`BinaryEntryIterator.fetch()`
(`hugegraph-server/hugegraph-core/src/main/java/org/apache/hugegraph/backend/serializer/BinaryEntryIterator.java:69-96`)
merges store records into the current `BackendEntry` and stops in three ways:

1. next record belongs to a new entry → buffered in `this.next`, position points at an unread key ✔
2. limit reached → it reads `limit + 1` records and `removeLastRecord()` (lines 89-95, comment
   *"Need remove last one because fetched limit + 1 records"*) ✔
3. current entry holds `INLINE_BATCH_SIZE` columns → `break` (lines 76-81) — **after** the record
   that triggered the check has been merged into the entry about to be emitted ✘

`INLINE_BATCH_SIZE` is `Query.COMMIT_BATCH` = 500 (`BackendEntryIterator.java:36`,
`Query.java:47`) — not `query.page_size`, they merely share the value. The backend iterator
updates `position()` in `hasNext()` to the key of the record it is about to return
(`RocksDBStdSessions.java:1133-1136`), so after case 3 the position is the key of an **emitted**
record. `BinaryEntryIterator.pageState()` (lines 117-123) builds `PageState(position, 0, count)`
with the offset hard-coded to 0, and the next page restarts **inclusively** from that position
(`BinarySerializer.prefixQuery` → `IdPrefixQuery(inclusive=true)`, `BinarySerializer.java:946-963`;
sort-key ranges force `includeStart = true` at `:704-713`). Hence the duplicate.

Why only multiples of 500: with `limit = L`, case 2 fires at record `L+1` and removes it — clean.
When `L mod 500 == 0`, case 3 fires at record `L` *before* record `L+1` is read; the consumer then
stops on `reachLimit` without calling `fetch()` again, so the position is never refreshed.
`QueryList.OptimizedQuery.iterator()` (`QueryList.java:166-183`) passes the user limit straight to
the store (*"Not set limit to pageSize due to PageEntryIterator.remaining"*), so `query.page_size`
never splits the scan — which is also why changing `query.page_size` cannot fix the edge case.

For the label-index path the sub-query limit **is** `query.page_size` (`IdHolder.java:124-138`,
`GraphIndexTransaction.doIndexQueryOnce` `:736-761`), and a label index keeps all element ids under
one index key — one huge entry → same stale position. This is why the core-test profile
(`query.page_size=2`) cannot reproduce the vertex variant and only the edge test is in the patch.

Upstream already knows the `position` semantics are inconsistent: four `// FIXME` blocks in
`BinarySerializer.java` (708-711, 824-827, 856-859, 954-957) disable the lower-bound assertion
*"due to the inconsistency in the definition of `position` of RocksDB scan iterator and Hstore"*,
and `HstoreSessionsImpl.ColumnIterator` (`:245-254`, `:318-328`) carries
`// QUESTION: Resetting the position ...` comments. The hstore-specific bookkeeping is why its
label-index duplicates land off the boundary. No test exercises ≥ 500 columns per entry
(`EdgeCoreTest.testQuery*EdgesOfVertexInPaging` use `limit(1)` on 18 edges), and the REST default
`limit=100` hides it in normal use.

### Fix
Check the full batch **before** merging the record and start the next entry with it
(`this.next = this.merger.apply(null, elem)`), so `position()` never points at an emitted record.
24 lines in `BinaryEntryIterator`, plus `EdgeCoreTest.testQueryOutEdgesOfVertexInPagingAtBatchBoundary`
(1 200 edges, page limits 400/500/600/1000, asserts count and distinct count).

Validation (`cluster/validate_patch.sh`): red on unpatched core, green with the patch; the patched
`hugegraph-core` jar deployed to **both** distributions: probe clean for every size and both paths
(`results/probe_patched_rocksdb.txt`); the full suite on the patched servers against the pre-fix
oracle report differs in **exactly one** case (`PAGED asset=ETC, page size 500`: 1214 → 1212, same
unique set — `results/compare_pre-fix_oracle_vs_patched_hstore.txt`); patched oracle vs patched hstore:
`OK=170 BOTH-ERR=4`.

## F2

**REST `properties` filter rejects string range predicates that Gremlin accepts**

Status: **unreported**; unclear whether by design.

`GET /graph/edges?...&properties={"asset":"P.gte(\"ETC\")"}` → `Invalid value 'ETC', expect a number`;
`P.between("ETC","ETC!")` → `Invalid value '"ETC","ETC!"', expect a list of number`; also for
`ÉTC` and `～`. Identical on both backends (`BOTH-ERR`). The same ranges via Gremlin
(`g.V('a').outE('flow').has('asset', gte('ETC'))`) return 1220 / 1213 / 3 / 1 on both backends
(section S, `GRMLN ... gte/between` cases). So the server supports string ranges on TEXT sort keys;
only the REST predicate parser assumes numbers. Worth an issue asking whether that is intended.

## F3

**hstore position bookkeeping differs from rocksdb (pre-fix duplicate positions)**

Status: **observation**, absorbed by F1.

Before F1 both backends duplicated on label-index paging, but rocksdb duplicated exactly at page
boundaries while hstore's six duplicates were elsewhere in the page. After F1 both are clean.
The difference comes from `HstoreSessionsImpl.ColumnIterator` prefetching one record in its
constructor and nulling `position` on exhaustion, and from multi-partition scans
(`HstoreTable.java:342`, `:391`). Any future change to `PageState.offset` handling has to be checked
on both backends.

## F4

**Inverted test guards: `assumeTrue("skip this test for hstore", isHstore)`**

Status: **known** (a `FIXME` in the tests points at #3090), not fixed.

29 tests in `hugegraph-test` carry

```java
// FIXME: The legacy HStore guard and related coverage debt are tracked in
// https://github.com/apache/hugegraph/issues/3090
Assume.assumeTrue("skip this test for hstore",
                  Objects.equals("hstore", System.getProperty("backend")));
```

`assumeTrue(isHstore)` runs the test **only** on hstore and skips it on every other backend —
the opposite of the message. Those tests contribute nothing to the rocksdb/hbase/... CI matrix.
Example: `VertexCoreTest.testScanVertexInPaging` (`VertexCoreTest.java:7739`).

## F5

**#3090 — hstore pushdown reads properties the server never wrote with a header**

Status: **reported** (issue #3090), interim mitigation **PR #3184** (ours).

Background of section S. The server serialises property values raw (core `BytesBuffer` ~L596)
while the store-side reader expects `(cardinality << 6) | dataType` (`hugegraph-struct`
`BytesBuffer` L565-606). Any condition pushed into the store on a sort-key range therefore fails
with `Can't construct Cardinality from code 0` or `Unsupported data type UNKNOWN`
(`results/edge_master.txt`; in the oracle suite on master: 37 `TARGET-ERR` in section S plus the same
shape via Gremlin in section J, `results/compare_master.txt`). PR #3184 pushes conditions only when user properties are present,
pushes a `copy()` and clears `originQuery` (payload 2275 → 657 bytes); with it every section-S
case passes (`results/edge_fix.txt`). The remaining #3090 work (codec parity, `HstoreStore.open()`
early-return via `useSession()`, the guards in F4) is not in this repository.

## F6

**PRs #3184, #3182 and #2994 do not interfere**

Status: **unreported** validation result — answers the coordination request in #3090 (test the
combined branch on the four PR territories). #3182 has since been merged (2026-09-03), so
`combined` equals today's master + #2994 + #3184.

`combined` = master `98477f0` + #3184 `1072872` + #3182 `acbee167` + #2994 `5cb9a519`
(recipe in [setup.md](setup.md#source-and-build)): 174 cases identical between hstore (3-store) and
rocksdb, `OK=170 BOTH-ERR=4` (`results/compare_combined.txt`). Section J (label semantics of #2994)
and section K (cache / within-search of #3182, including the #3180 combo ×10) show no divergence;
section L (paging across three store partitions, #3140 territory) is clean; section S contains the
six #3184 reproducer shapes among its 56 cases.

## F7

**Semantics confirmed, not bugs** (kept so nobody re-investigates them)

- New vertices added in a Gremlin script are visible to queries in the same script before
  `tx().commit()`; the commit changes nothing (section K5, both backends).
- Every second run of a section-K query (cache hit) returns the same set as the first, on both
  backends, including the #3180 combo.
- Gremlin string comparison is UTF-16 order on both backends (see pitfalls).
- Sort-key prefix equality is exact: `asset=ET` does not swallow `ETC*`, trailing space, case and
  `!` inside a value are all distinct (section S-B).

## F8

**hstore on master honours only the first `within()` value when a SEARCH-index condition is present**

Status: **fixed upstream by PR #3182** (merged 2026-09-03) — this is the before/after evidence.

On master `98477f0`, hstore 3-store vs rocksdb (`results/compare_master.txt`), section K:

| Query | rocksdb | hstore (master) | hstore (combined) |
|---|---|---|---|
| `type>=2 & confirmType within(1,2,3) & Text.contains('诚信')` | 50 | **20** | 50 |
| `type>=2 & confirmType within(1,2,3) & Text.contains('gold')` | 60 | **20** | 60 |
| `type>=2 & confirmType within(1,2,3,4) & contains('gold')` | 80 | **20** | 80 |
| `type within(1,3,5) & contains('gold')` | 60 | **20** | 60 |
| `confirmType within(1,3) & contains('gold')` | 40 | **20** | 40 |
| `type>=2 & confirmType within(1,2,3)` (no search) | 40 | 40 | 40 |
| `confirmType within(2) & contains('gold')` | 20 | 20 | 20 |

The missing ids are always the elements matching the second and later `within()` values
(`only-oracle=30/40/60/20`, `only-target=0`): the flattened `within` sub-queries are collapsed to the first
one when a search-index post-filter is involved. No error is raised — the caller gets a wrong answer.
The cache hit (second run) returns the same wrong set. In the K5 transaction test hstore additionally
returns one element fewer after `commit()` than before (`after − before = −1`, rocksdb 0). All 16 cases
are identical sets on `combined`.

## F9

**`hasLabel(without(…))` with a range-index property throws on master — on every backend**

Status: **fixed upstream by PR #2994** (open) — before/after evidence; server-level, not backend.

Master `98477f0`, both rocksdb and hstore (`BOTH-ERR`):

```
g.V().has('age',gte(30)).hasLabel(without('person','robot'))
  -> Can't do index query with [LABEL != 5, LABEL != 6] and [12 >= 30]
g.V().hasLabel(without('person')).barrier().has('age',gte(60))
  -> Don't accept query based on properties [age] that are not indexed in any label, may not match range/not-equal
```

On `combined` both return results (0 and 100 vertices) identically on both backends. These are the only
two cases in which the rocksdb results of master and `combined` differ at all
(`results/compare_master-oracle_vs_combined-oracle.txt`: `OK=168 ORACLE-ERR=2`), i.e. #2994 and #3182
introduce no other semantic change visible to this suite.

## F10

**`usePD=true` server cannot start on a JVM whose default locale uses a decimal comma**

Status: **unreported**; one-line fix.

`GraphSpace.info()` (`hugegraph-server/hugegraph-core/src/main/java/org/apache/hugegraph/space/GraphSpace.java:392-394`)
does

```java
float storageUserPercent = Float.parseFloat(
        String.format("%.2f", (float) this.storageUsed / ((float) this.storageLimit * 1.0)));
```

`String.format` without a `Locale` formats with the JVM default locale; on `pl_PL`, `de_DE`, `fr_FR`, `ru_RU`, …
that yields `"0,00"`, which `Float.parseFloat` rejects. `info()` is called whenever a `GraphSpace` is serialised
(`HugeGraphSONModule.GraphSpaceSerializer`), so with `usePD=true` the server dies during
`GraphManager.loadMetaFromPD()` → `createDefaultGraphSpaceIfNeeded()`:

```
[ERROR] o.a.h.c.GraphManager - Unable to load meta for PD server and usePD = true in server options
Exception in thread "main" java.lang.IllegalStateException: org.apache.hugegraph.HugeException: Can't write json: For input string: "0,00"
Caused by: java.lang.NumberFormatException: For input string: "0,00"
```

Reproduced on master `98477f0` and on PR #3157's head with `LANG=pl_PL.UTF-8`; `LC_ALL=C.UTF-8` for the JVM makes
it start. The same code path serves `GET /graphspaces/{space}` responses, so REST calls that serialise a graph
space are affected on such locales as well. It is the only `String.format("%.Nf")` without a `Locale` in the
server, pd and store main sources. Fix: `String.format(Locale.ROOT, …)` or `Math.round(x * 100) / 100f`.

### Addendum 2026-09-05 — on an existing PD the server does not fail fast: it starts *degraded*, and `dropGraph` is broken

F10 was documented as "the server cannot start". On master `98477f0` (`dist-master-hstore`) with the DEFAULT graph
space **already present in PD**, a JVM started with `LANG=pl_PL.UTF-8` and no `LC_ALL` **does start** — REST and
Gremlin answer, the graph list is served from PD meta — but it is silently degraded:

- `GraphManager.loadMetaFromPD` logs `Failed to load graph '<g>' from meta server` for a subset of the graphs
  (7+ of 19 here: `rbug`, `lnq`, `ctrlx`, `master_pdmeta_g2/g3`, `pr3157_pdmeta_g2/g3`); the startup log carries
  39 × `"0,00"` and 13 × `NumberFormatException`/`JsonMappingException`.
- Task schedulers are not registered: `DELETE /graphs/{g}` fails with
  `IllegalStateException: Can't find task scheduler for graph 'standardhugegraph[DEFAULT-<g>]'`
  (`StandardHugeGraph.taskScheduler:1226` ← `waitUntilAllTasksCompleted:1325` ← `clearBackend:514` ←
  `GraphManager.dropGraph`) — also for a graph **created in this JVM after start** (`f12big`), and it does not
  recover with time.
- Other drops die on the F10 exception itself: `SpaceMetaManager.updateGraphSpaceConfig:100` →
  `Can't write json: For input string: "0,00"`.

Fail-fast happens only when `createDefaultGraphSpaceIfNeeded()` has to serialise a *new* space (fresh PD). With an
existing space the same unlocalised `String.format` degrades the server instead: some graphs never load, every drop
fails, and nothing at the REST level says so until you try. Evidence: `results/f10-degraded-start-pl_PL.log`.
Workaround unchanged — `export LC_ALL=C.UTF-8` before the start script; the lab's server-restart helper
now enforces it and aborts if the startup log shows any `Failed to load graph` / `0,00` line. This moves F10 from a
start-up nuisance to a **silent partial outage on any non-English-locale host that already has a PD meta store**.

## F11

**PD KV metadata watches die after a PD restart on master — fixed by PR #3157 (before/after)**

Status: **reported upstream by contrueCT as #3152**; PR #3157 open; this is the live validation.

Setup (`cluster/make_dist_pair.sh`, `cluster/pd_watch_exp2.sh`): PD + 3 stores, two servers from the same build
sharing the PD in PD-meta mode (`usePD=true`), A :8080 and B :8082. Probe = A creates graph `g`, B constructs it on
first access (lazy load), A drops `g`, B must drop its instance on the `GRAPH/REMOVE` watch event. PD is stopped
for 30 s and restarted between probes.

| Step | master `98477f0` | PR #3157 `f99b6bd` |
|---|---|---|
| baseline | B dropped after 1 s | B dropped after 1 s |
| after PD outage #1 | **B still serves the dropped graph after 60 s**, no add signal → watch dead | B dropped after 1 s |
| after PD outage #2 | **still stale after 60 s** | B dropped after 1 s |

Master's `KvClient.onError` re-issues `listen()` once and `onCompleted` is empty; after a PD restart nothing is
logged and no watch comes back. The PR retries every ~1 s per subscription during the outage
(`Failed to reconnect watch for key HUGEGRAPH/hg/EVENT/GRAPH/{ADD,REMOVE,UPDATE,CLEAR,SCHEMA/CLEAR}`) and
re-registers all five within a second of PD listening again (`set watch client id` ×5). Side remark sent with the
results: every failed attempt logs a full stack trace — ~210 per 30 s outage per server.
Logs: `results/pd_watch_3152_{master,pr3157}_{drop,add}.log` (the `_add` variant is the first attempt whose
graph-add observable turned out to be compensated by lazy loading — kept because its `Accept graph add signal`
counts are the add-side evidence: master 1/0/0, PR 1/1/1).


## F15

**A stalled store node holds a REST worker for up to `11 × grpc.timeout.seconds + 38 s` on every write, and `restserver.request_timeout` cannot stop it**

Status: **unreported**; patch is small.

`NodeTxExecutor.retryingInvoke()` (hg-store-client) wraps the commit of a transaction in a retry loop with
`NODE_MAX_RETRYING_TIMES = 10` — a constant in `HgStoreClientConst`, not a configuration option — and a sleep
schedule of 1, 1, 1, 2, 3, 4, 5, 6, 7, 8 s between attempts. Each attempt is a blocking gRPC `batch` call with
the stub deadline `grpc.timeout.seconds` (`HgStoreClientConfig`, default 100 s). When the partition leader does
not answer, every attempt ends in `DEADLINE_EXCEEDED` and the loop runs to the end: 11 attempts × 100 s + 38 s
of sleep ≈ 19 min per request on defaults, ≈ 12 min with `grpc.timeout.seconds=60`.

The loop catches `InterruptedException` from `Thread.sleep`, logs `Failed to sleep` and continues. So the
interrupt that `restserver.request_timeout` (default 30 s) sends to the Grizzly worker is swallowed, and the
REST server's own request limit is ineffective on exactly the path where it is needed. A single frozen store
therefore ties up REST workers for minutes each; with a multi-threaded writer this is the "the whole REST server
stopped responding" report.

Reproduction (`cluster/repro_deadline.py`, `results/deadline-repro-2026-09-09.log`): `kill -STOP` the store
process on one node, run concurrent `PUT /graph/vertices/batch` upserts of existing ids spread over all
partitions. One writer whose read phase happened to avoid the frozen store went on to the commit and hung
for 300 s until the client gave up; the server log shows `DEADLINE_EXCEEDED: deadline exceeded after 99.999s`
on `HgStoreSessionGrpc$HgStoreSessionBlockingStub.batch` from `GrpcStoreNodeSessionImpl.doCommit`, then
`NodeTxExecutor - Failed to sleep` (the swallowed interrupt), then the next attempt. `kill -CONT` restores the
cluster instantly; the next upsert succeeds in 0.0 s.

### Before/after measurement of the fix (2026-09-10, `patches/0002`, `results/f15/`)

`cluster/repro_rate.py`: one single-vertex `POST /graph/vertices` per second for 300 s (each in its own thread,
roughly one third of them land on the frozen store's partitions), then no more writes; a probe `GET
/graph/vertices/"p00010"` every 2 s until the server answers 10 in a row. The store on node3 is frozen with
`SIGSTOP` for the whole run. master `36811483`, hstore dist, 8 CPU = 16 REST workers, `restserver.request_timeout`
default 30 s, `grpc.timeout.seconds=20` written into both client jars so that one run fits in minutes
(with the default 100 s the "before" numbers are 5× longer).

| | before (`hg-store-client` as in master) | after (`patches/0002` applied) |
|---|---|---|
| REST unavailable (probe gets 503 from `LoadDetectFilter`) | from 28 s to 254 s and from 285 s to 503 s: **431 of 503 s** | four blips of 2–6 s, **12 s of 300** |
| REST still dead after the writer stopped | **200 s** | 0 s |
| POST outcomes, 300 sent | 27 × 201, **243 × 503 rejected**, 23 × 500 | 131 × 201, 152 × 500 after exactly 20.0 s, 7 × 503 |
| slowest POST | **257 s** (= 11 × 20 s + 38 s of sleep) | 20.1 s (one deadline) |
| server log | `Failed to sleep` 30, `reached the upper limit` 30, `for the next try` 300 | `Failed to sleep` 0, `Not retrying after` 152 |

So without the fix a writer at 1 request/s turns a single frozen store into a REST server that answers 503 to
everything for as long as the writes last plus another 3–4 minutes (19 minutes with the default deadline), and
the writes to healthy partitions are rejected together with the rest. With the fix every request that touches
the frozen partition fails after one deadline, the pool never fills, writes to healthy partitions keep
succeeding, and the server is back to normal the moment the writer stops. `kill -CONT` afterwards needs no
restart in either case. A second scenario (`cluster/repro_rest_dead.py`, 24 writers in a tight retry loop, in
`results/f15/loop-*`) shows the limit of the fix: a client that re-sends immediately after every error keeps
16 workers busy with 20–100 s calls on its own; there the fix only cuts the swallowed interrupts to zero and
triples the successful writes (29 → 96 in 240 s), the client-side backoff is still needed.

Fix: in `retryingInvoke` restore the interrupt flag on `InterruptedException` and abort the loop with an
error; stop retrying on `DEADLINE_EXCEEDED` at all (a second attempt only waits the full deadline again — the
retry is only useful for `UNAVAILABLE` after a leader change, which is what PR #3130 relies on); expose the
attempt count and schedule in `HgStoreClientConfig` with a much smaller default. Configuration-only
mitigation until then: `grpc.timeout.seconds=10..15` (a healthy store answers these calls in milliseconds),
which bounds the worst case to about 3 minutes.

## F16

**Multi-id reads (`getWithBatch` → `batchPrefix` → `scanBatchOneShot`) have no retry, no failover and no isolation of a single stalled store**

Status: **unreported**; matches a production log of 2026-09-09 (`PUT /graph/vertices/batch` → 500
`DEADLINE_EXCEEDED: deadline exceeded after 59.97s ... remote_addr=<one store>:8500`, in
`HgStoreStreamGrpc$HgStoreStreamBlockingStub.scanBatchOneShot` via `blockingUnaryCall`).

`PUT /graph/vertices/batch` (upsert) first reads the existing vertices with `g.vertices(ids)`. On HStore an
`IdQuery` with more than one id goes through `HstoreTable.query()` → `session.getWithBatch()` →
`HgKvStore.batchPrefix()`, which groups the keys by store node and issues **one blocking unary
`scanBatchOneShot` per store** (`KvBatchOneShotScanner`), with the stub deadline `grpc.timeout.seconds` and no
retry (`batchPrefix` does not go through `NotifyingExecutor`). The store side (`ScanBatchOneShotResponse`)
collects the whole result before answering. One store that does not answer fails the entire read after the
full deadline, including the ids that live on healthy stores, and the REST worker is blocked for that long.
The 60 s in the production log is a non-default `grpc.timeout.seconds=60` (defaults are 100 s in 1.5.0,
1.7.0 and master) combined with a `restserver.request_timeout` of 60 s or more.

On the lab (defaults): with one store frozen, 12 concurrent upserts hung for 30.9 s and ended with 500
`CANCELLED: Thread interrupted` (here `request_timeout=30` did fire, because the read path does not swallow
the interrupt), 7 more were rejected immediately by the batch-write limit (`The rest server is too busy to
write`, `batch.max_write_ratio`) or by `LoadDetectFilter` (503). Point reads (`GET /graph/vertices/"id"`)
and `/versions` kept answering in milliseconds throughout; every scan crossing the frozen partition
(`GET /graph/vertices?label=...&limit=1`) timed out.

Related but not a fix: PR #3128 (each stub on its own channel, merged 2026-07-31) reduces queueing under load;
PR #3130 (merged 2026-08-15) retries only `UNAVAILABLE` after a store replacement. Fix direction: issue the
per-store one-shot scans in parallel with a short deadline of their own (these are point prefix lookups), on
`DEADLINE_EXCEEDED` refresh the partition leader from PD once and retry that store only, and make the
one-shot path honour the caller's deadline. Whether a partial result should ever be returned is a product
decision — REST has no partial-success semantics today — so the safe change is a fast, complete failure.

F12–F14 are tracked outside this repository.
