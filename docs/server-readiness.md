# Storage-aware Server readiness (apache/hugegraph#3212) on the Helm chart, 2026-09-18

Follow-up to `helm-chart-faults.md`, finding 1: the Server readiness probe was `GET /versions`, which
answers 200 with no Store in the cluster, so a Kubernetes Service kept routing to Servers whose every
graph request failed. This documents the `GET /readiness` endpoint proposed under #3212, how it was
tested on pods, and what the tests changed in its design.

## What was under test

| item | value |
|---|---|
| chart | `hugegraph/hugegraph` branch `feat/hstore-helm-chart-3132` (apache/hugegraph#3218, hugegraph/hugegraph#221), commit `05f3d9e`, plus one local commit adding `server.readinessPath` (see below) |
| images | PD `hugegraph/pd@sha256:6f4cee85…`, Store `hugegraph/store@sha256:879ee2d1…` (`:latest` built 2026-09-18 from master after #3210/#3157/#3164/#3213/#3216); Server `hugegraph/server@sha256:d03380ae…` for the "before" run, and the same image with `hugegraph-api-1.7.0.jar` and `hugegraph-hstore-1.7.0.jar` replaced by the build of branch `feat/server-readiness` (tags `3212`…`3212f`, imported with `k3s ctr images import`) for the rest |
| cluster | k3s `v1.36.4+k3s1`, 3 nodes (`hg-k3s-1..3`), `values-cluster.yaml`: 3 PD + 3 Store + 3 Server, PD `raft.rpc-timeout=3000` |
| data | the oracle of the fault battery (20 000 vertices, 100 000 edges); the load during faults is the same writer + reader through the Service |
| probes | chart defaults: readiness `periodSeconds: 10`, `failureThreshold: 3`, `timeoutSeconds: 5`; Server `readiness.timeout=1000`, `readiness.cache_ttl=2000` (defaults) |
| harness | `cluster/k3s_readiness.py` on top of `cluster/k3s_faults.py`: 1 Hz samples per Server pod of `GET /readiness`, `GET /versions`, the pod Ready condition and the Service endpoints; per-scenario JSON and samples in `results/issue-3212/` |

## The endpoint under test

`GET /readiness`, unauthenticated (whitelisted in `AuthenticationFilter` and `PathFilter`), 200 or 503:

```
{"ready":true,"storage":"hstore","reason":"ok","active_stores":3,"answered_store":6386607161404282741,
 "pd_reachable":true,"pd_checked_age_ms":312,"stores_age_ms":312,"store_millis":3,"cached":false}
```

Ready means: at least one Store from the last store list PD answered with answers a direct, local
gRPC call (`HgStoreState.getScanState`, a read of the node's own scan-pool stats). PD is refreshed in
the background on every probe and never waited for once a list is known. One shared time budget
(`readiness.timeout`) bounds everything; the result is cached for `readiness.cache_ttl`.

## Results

| # | scenario | fault landed | readiness during the fault | verdict |
|---|---|---|---|---|
| 1 | baseline, 60 s, no fault | n/a | 183/183 samples 200, p50 4 ms, p99 10 ms, cache ratio 0.64 | PASS |
| 2 | all Stores scaled to 0, then back to 3 | pods gone, DNS names unresolvable | every Server 503 within 1-2 s, `NotReady` and out of the Service by 29-32 s (kubelet's `failureThreshold: 3 × periodSeconds: 10`); Service endpoints reach 0; back to 200 in ~17 s and Ready ~20 s after the Stores return | PASS |
| 3 | one Store pod force-deleted | new pod uid, back Ready in 23 s | 222/222 samples 200, zero readiness or Ready-condition transitions; load 274 writes / 274 reads, 0 failed | PASS |
| 4 | rolling restart of the Store StatefulSet under load | 3 Stores replaced one by one | 351/351 samples 200, zero transitions, all three Store ids answered across the roll; probe latency max 11 ms; load lost 1 write to the 30 s bound | PASS |
| 5 | all PD scaled to 0 (90 s), then back | no PD leader, `getActiveStores` fails | every Server stays 200 throughout (`codes_during_fault: [200]`), the body flips `pd_reachable` to false after ~42 s and back to true ~19 s after PD returns; zero Ready-condition transitions | PASS |
| 6 | rolling restart of PD under load | 3 PD replaced one by one | 411/411 samples 200, zero readiness and zero Ready-condition transitions; probe latency max 9 ms | PASS |
| 7 | SIGSTOP the Store leading the most partitions (24/24 or 14), ~60 s | `/proc/<pid>/stat` = `T`, container restarted by its own liveness probe | 327-336 samples 200, zero readiness transitions, all three Store ids answered (raft moves the leaders off the frozen Store) | PASS |

Summary: 7 PASS, 0 FAIL. No acknowledged write was lost; the load failures are the known #3204 30 s request bound during the store/PD churn, unrelated to readiness.

## What the tests changed in the design

Two earlier probe designs failed exactly these scenarios, which is why the endpoint looks the way it does:

1. **`existsTable` on a store node session flapped on a roll.** The first probe pinged each active Store with the store client's own `existsTable` (a raft-routed `Table/EXISTS`). During a Store roll and a PD roll every Server went 200 → 503 → 200 several times (`store-roll` 23 × 503, `pd-roll` 72 × 503, run `run-3212d-existsTable/`), because the shared store-client session pool and the raft round-trip made a healthy node's ping wait behind a leaving one. Replaced by a direct, local, read-only gRPC call to each node (`HgStoreState.getScanState`, the node's own scan-pool stats, no raft) over the probe's own plaintext channels, with the pings fired in parallel and the first answer winning.

2. **Probing PD on every request made readiness track PD, not storage.** With PD as a hard dependency, `pd-zero` and `pd-roll` turned Servers `NotReady` while the data plane was serving fine. Now PD is refreshed in the background and the last known Store list is used immediately; PD being down, slow or restarting only flips the reported `pd_reachable`, never the readiness, as long as a Store answers (scenario 5).

## Chart change

`chart-server-readinessPath.patch`: adds `server.readinessPath` (default `/versions`) to `values.yaml`, `values.schema.json` (`definitions.server`) and the Server readiness probe in `server-deployment.yaml`, mirroring `pd.readinessPath`. Set `server.readinessPath=/readiness` on an image that serves it. Startup and liveness stay on `/versions`, so a Server that only lost its storage drops out of the Service but is not restarted.

## Runs

`final-3212f/` the table above; `run-3212d-existsTable/` and `run-3212e/` the two superseded designs (kept as the evidence for the two changes); `before-latest/` the shipped image, where `/readiness` is 404/401 (endpoint absent). The Server image is the shipped `hugegraph/server:latest` with the two rebuilt jars overlaid; the exact classes are the `feat/server-readiness` branch.

## Review round 1 (2026-09-19)

Reviewer findings on apache/hugegraph#3221, all fixed in the PR: the `reason` field embedded raw exception text
(PD peers via `PD unreachable, pd.peers=...`, Store host names via gRPC `Unable to resolve host ...`) on an
unauthenticated endpoint; the background PD refresh was not single-flight (a hung PD parked one thread per
cache-missed probe); channels of replaced Stores were never shut down; docs still described the pre-redesign
probe. `round1-3212g/` reruns `stores-zero` and `pd-zero` on the fixed build: both PASS, and a grep of every
sampled body for `.svc`, `8686` and `hugegraph-store-` finds 0 occurrences (before: the reasons quoted the Store
DNS names). The 503 reasons are now categories only, e.g.
`none of 3 known store(s) answered: a store failed: UNAVAILABLE; a store failed: UNAVAILABLE; ...`.

## Review round 4 (2026-09-30): a read through the graph path as the gate, measured and rejected

The reviewer asked that `ready` prove the graph-serving path (raft, partition, RocksDB), not only that a
Store's status RPC answers. Three gates were built on top of the round-3 probe and run through the same
seven scenarios, Server image = shipped `latest` with the rebuilt api + hstore jars (`3212j`..`3212m`):

| tag | gate | store-one-down | store-roll | store-freeze | pd-zero (load failed) | pd-roll (load failed) |
|---|---|---|---|---|---|---|
| `3212j` | ping + one get of a sentinel key on the vertex table through the query Store client, one shared partition | PASS | 503 on 3/3 Servers at once, 8-35 s | 503 on 3/3, 4 s | 503 for the whole outage (87 %) | 503 on 3/3, 28-36 s (7 %) |
| `3212k` | ping + the same read in a partition led by the Store that answered the ping (leader codes from PD's partition cache at each refresh) | PASS | 503 on 3/3, 23-42 s | 503 on 3/3, 20-28 s | 503 (58 %) | PASS (0 %) |
| `3212l` | ping + parallel reads, one partition per known Store, first success wins | 503 on 3/3, 3-12 s, four times | 503 on 3/3, 13-35 s | 503 on 3/3, 3-7 s | 503 (62 %) | 503 on 3/3, 53-85 s (47 %) |
| `3212m` | round-3 ping gate + single-flight `StorageReadiness.check` (the PR head) | PASS | PASS | PASS | PASS (2 / 244) | PASS (1 / 370) |

Two effects, present in every read-gated variant (`round4-graphread-3212{j,k,l}/`, control `round4-final-3212m/`):

1. **The reads do not fail independently.** Every 503 reason is `... partition read(s) did not finish within
   1000 ms`, also when two of three Stores are healthy and their partitions are being read. On the first
   error the Store client invalidates its partition cache and sleeps 1 s before the retry
   (`NodeTxExecutor.retryingInvoke`, `HgStoreNodePartitionerImpl` on a `NOT_WORK` notice), so one moving
   partition stalls every read through that client past the probe budget. During a Store roll all three
   Servers went 503 together and were pulled out of the Service while the load through the Service had 2-3
   failures in ~45 requests.
2. **A read through the query client is not a passive measurement.** A failed probe read invalidates the
   partition cache shared with real queries, and with PD down there is nothing to rebuild it from: in
   `pd-zero` the load failed 2 of 244 requests with the ping gate and 387-560 of ~640 with any read gate; in
   `pd-roll` 1 of 370 versus 106 of 226 (`3212l`). A readiness probe that takes the data plane down during a
   PD outage is worse than one that cannot see raft.

Outcome: the gate stays on the ping; its scope is stated in the Javadoc and the PR ("this Server knows a
Store list and reaches at least one Store over gRPC; a Store whose status RPC answers while its raft path
is broken is not detected"). A Store-side signal that covers raft without going through the shared client
does not exist today (`HgStoreState.getPeers` is in the proto, not implemented in the store node); it is
offered as a separate Store PR. The single-flight change from this round is kept: `StorageReadiness.check`
no longer holds a monitor during I/O, concurrent callers share the running probe with a bounded wait.
