# Rolling upgrade under load, cluster preset (2026-09-20)

Chart `feat/hstore-helm-chart-3132` at `05f3d9e`, release `hugegraph` (3 PD + 3 Store + 3 Server, `raft.rpc-timeout=3000`,
store limits 8Gi), PD/Store `:latest` of 2026-09-18, Server overlay images `3212g`/`3212h` (two builds of #3221).
Harness `cluster/k3s_rolling_upgrade.py`: the fault-battery writer + reader run through the Service the whole time
(oracle 20 000 vertices + 5 M padding vertices), the components are upgraded one at a time in the order of the
operations guide (Store → PD → Server) the way a chart upgrade does it (`helm upgrade`, no `--wait`, then
`kubectl rollout status` per component), with 1 Hz samples of pod Ready, Server Service endpoints, PD leader,
partition leaders and Server `/readiness`; the oracle is verified after each pass.

| pass | what changed | Store roll | PD roll | Server roll | writes (fail) | reads (fail) | min Server endpoints | PD leaderless samples | oracle |
|---|---|---|---|---|---|---|---|---|---|
| 1 `pass1-restart/` | pod annotation only (restart of all three, Server image unchanged) | 66 s | 295 s | 46 s | 2205 (2) | 2205 (0) | 3 | 0 | 0 / 0 |
| 2 `pass2-to-3212g/` | Store, PD restart; Server image `3212h` → `3212g` | 66 s | 95 s | 47 s | see JSON (1 per Store/PD step) | 2 during the Store roll | 3 | 0 | 0 / 0 |
| 3 `pass3-to-3212h/` | Store, PD restart; Server image `3212g` → `3212h` | 64 s | 76 s | 41 s | 1 during the Store roll | 1 during the Store/PD roll | 3 | 0 | 0 / 0 |

Observations:

- No acknowledged write was lost and every oracle sample matched after each pass; the Server Service never dropped
  below 3 endpoints (the Deployment surges before it terminates) and PD never went leaderless in any 1 Hz sample.
- Every failed request is the known 30 s request bound (#3204): during a Store roll, a read or write that lands on a
  partition whose leader was on the pod being restarted waits the full 30 s and fails once, then the load recovers.
  That is 1-2 requests per pass; the harness's single-threaded load makes the per-step request counts small when it
  is stuck in such a wait (e.g. 6 requests during a 66 s Store roll in passes 2-3).
- PD roll time varies a lot: 295 s in pass 1 vs 76-95 s in passes 2-3, all three with the same `/v1/ready` readiness
  gate; pass 1 ran right after the Store roll had moved every partition leader, worth watching in future passes.
- The Server image change itself (passes 2 and 3) is invisible to clients: 0 failed requests during both Server
  rolls, `/readiness` 200 on every pod from the first sample after it became Ready.
