# HugeGraph Helm chart (apache/hugegraph#3132): fault battery on pods, 2026-09-16

Independent run of the chart on a three-node k3s cluster, with the fault scenarios we had scripted on VMs
re-done on pods. Not a re-run of the chart author's own campaign (his harness is an agent skill; reusing it would
find the same things), but the same discipline: oracle written before the fault, proof that the fault landed,
verdict with a blame class.

## What was under test

| item | value |
|---|---|
| chart | `bitflicker64/hugegraph` branch `feat/hstore-helm-chart` (the #3132 branch), commit `895019159439a7ea0d3a35c20e656ac53c554004`, `Chart.yaml` version 0.1.0 |
| images (`:latest` from Docker Hub, per the author: built from apache master `af10f44f`, i.e. with #3204) | `hugegraph/pd@sha256:e5f72158849301e194c6ab939f8f8111e4da544000a69f890dfe8daa2b068470`, `hugegraph/store@sha256:0e0623a59ced0aefe0e5680219d52fba50c3568712186180fc84f114113c4929`, `hugegraph/server@sha256:5e437813753122c970949fea627105eeb65fec3255fb2130438f9d98372e7127` |
| cluster | k3s `v1.36.4+k3s1`, 3 nodes (`hg-k3s-1..3`, 6 vCPU / 16 GB / 56 GB each, Debian 13, kernel 6.12), StorageClass `local-path`, one server + two agents |
| presets | `values-single.yaml`: 3 pods Running in 98 s, `helm test` Succeeded, REST `/versions` + schema write/read OK; `values-cluster.yaml`: 3 PD + 3 Store + 3 Server Running in 77 s, about 3.8 GB RAM and 1.6-2 cores per node at rest |
| data | 20 000 vertices (`node`, primary key `name`, property `w`) and 100 000 edges (`link`, sort key `seq`), loaded through `PUT .../batch`; oracle = the generator seed, checked by sampling 400 vertices with their exact out-edge sets after every scenario; every write acknowledged (HTTP 201) during a fault is read back afterwards |
| load during faults | one writer (20 new vertices per request) + one reader (a random oracle vertex and its 5 edges), `restserver.request_timeout` at the image default (30 s) |
| harness | `cluster/k3s_faults.py` (scenarios), `cluster/k3s_coldstart_fresh.sh` (#3203 shape), logs and per-scenario JSON in `results/helm-chart/` |

## Results

| # | scenario | fault landed (evidence) | data plane during the fault | recovery | verdict |
|---|---|---|---|---|---|
| 1 | Store pod force-deleted (`store-1`) | new pod uid, same node | writes 354 / 0 failed, max 5.2 s; reads 354 / 0 | Store Ready again after 23 s | PASS |
| 2 | Store JVM `SIGSTOP` 120 s, victim `store-1`, which led **0** partitions at the time | `/proc/<pid>/stat` = `T`; liveness (`/v1/health`, 20 s × 3) killed the container after ~50 s, restart count 1 | writes 537 / 0, max 0.5 s; reads 0 failed | container restarted by kubelet | PASS, but silent: a follower-only Store stall is invisible to clients |
| 3 | Store JVM `SIGSTOP` 120 s, victim `store-2`, which led **14 of 24** partitions | state `T`; liveness kill after 63 s (exit 143), restart 1; leaders moved to `store-0`/`store-1` (12/12) | writes 352 / 0 failed, max 4.3 s; reads 353 / **1 failed**: one `GET` hit the 30 s request timeout and came back `500 Interrupted while waiting to retry` (the #3204 interrupt path) at t+29.9 s | after `SIGCONT` 411 / 0, max 0.2 s | PASS: the F15 shape that held a REST worker for up to 19 min on VMs is bounded on pods to one 30 s request plus a 63 s kubelet restart |
| 4 | PD leader pod force-deleted (`pd-2`) | new uid; new PD leader after **31.9 s**; pod Ready again 9.5 s | writes 1 failed (30.5 s timeout), reads 0 | a property key created through server A is visible through B and C (200) within the 30 s window, so the F11 metadata-watch loss did not show | PASS |
| 5 | PD leader JVM `SIGSTOP` 90 s, default `raft.rpc-timeout` (10 000 ms) | state `T`; **no PD leader from t+12.8 s to t+56.2 s** (every sample), new leader at 60.6 s, which coincides with the liveness kill of the frozen PD (60 s after the stop) | writes 680 / 0, max 0.5 s; reads 0 | PD container restarted, rejoined as follower | PASS for the data plane; the PD control plane had no leader for about 60 s, as the author measured with a blackhole (67 s) |
| 6 | same, with `pd.javaOpts="-Draft.rpc-timeout=3000"` (the author's untested suggestion) | `-Draft.rpc-timeout=3000` on the PD command line; state `T` | writes 671 / 0, max 3.4 s; reads 0, max 2.6 s | **new PD leader after 8.5 s**, zero leaderless samples | PASS: the suggestion works, 60 s → 8.5 s |
| 7 | existing cluster: Stores scaled to 0, Server Deployment rolled | Server pods `Running`, **`ready=true`**, 0 restarts after 150 s with no Store at all | `GET /versions` 200 while `GET /graph/vertices/<id>` → `500 Interrupted while waiting to retry` after 30 s | Stores back → Ready in 23 s, Servers unchanged | PASS for the restart rule (no exit); finding on readiness below |
| 8 | fresh install (#3203 shape): chart installed, Stores scaled to 0 within 1 s, before any registered | Server pods `ready=false` for 244 s, 0 restarts, log `[wait-storage] No Up store yet, retrying in 5s` every 5 s | n/a (first boot) | Stores scaled to 3 → Servers Ready 20 s later; `/graphs` 200, property key 202, vertex label 201, vertex 201 | PASS: the chart's storage gate holds the Server where the bare image exited 1 (#3203) |

Verdict summary: 8 PASS, 0 FAIL, 0 INCONCLUSIVE. No acknowledged write was lost in any scenario and every oracle
sample matched afterwards.

## Findings for the chart

1. **Readiness does not reflect the data plane.** Server readiness is `GET /versions`, which answers 200 with no
   Store in the cluster (scenario 7). A Service then routes to Servers that answer `500` after 30 s to every graph
   request. A readiness path that touches the storage (e.g. the graph list from PD plus one cheap store call) would
   take such a Server out of the Service. Blame: chart (probe choice).
2. **`raft.rpc-timeout=3000` should be the PD default in the chart.** With the image default a frozen PD leader
   leaves the cluster without a PD leader for ~60 s and the new leader only appears when the liveness probe kills the
   frozen one; with 3000 ms the election takes 8.5 s. The data plane was unaffected in both runs, but anything that
   needs PD (schema, new partitions, Server registration) waits the full minute. Blame: chart default.
3. **The liveness probe is what ends a stall.** For a Store frozen with `SIGSTOP`, `/v1/health` fails and kubelet
   restarts the container after 50-63 s. The #3204 retry bound (one deadline retry, interrupt on request timeout)
   showed up as exactly one 30 s request in scenario 3; the rest of the load never noticed because raft moved the 14
   leaders off the frozen Store within seconds. Worth stating in the README: the 20 s × 3 liveness budget is the
   upper bound of a stalled Store's blast radius. Blame: none (working as designed), documentation.
4. **`values.schema.json` refuses `store.replicas=0`**, so "install PD + Server first, Stores later" cannot be
   expressed in values; the cold-start test had to scale the StatefulSet right after install. Fine as a guard rail,
   but the README could say how to stage a rollout. Blame: chart, minor.
5. **Naming**: for a release not named `hugegraph`, workloads and Services are `<release>-hugegraph-*`, but the
   Secrets are `<release>-admin`, `<release>-auth-token`, `<release>-pd-auth`. The install notes use the fullname
   for one and the release name for the other; cost me one failed check. Blame: chart, cosmetic.

## Not covered yet

The snapshot/compaction race (#3162) on pods (needs the Store test endpoints and unflushed data, as on the VMs),
a full PD disk (F18) on `values-single.yaml`, rolling upgrade under load, and a NetworkPolicy variant of the PD
blackhole (the chart has no NetworkPolicy support yet).
