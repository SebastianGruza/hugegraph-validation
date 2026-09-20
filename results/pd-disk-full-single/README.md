# Full PD disk on the single preset (2026-09-20)

Chart `hugegraph/hugegraph` `feat/hstore-helm-chart-3132` at `05f3d9e`, `values-single.yaml` (1 PD + 1 Store + 1 Server),
release `hg-single`; images `pd@sha256:6f4cee85…`, `store@sha256:879ee2d1…` (`:latest` of 2026-09-18) and the Server
overlay `3212h` (#3221 build) with `server.readinessPath=/readiness`. The PD data path sits on a 512 MB loop-backed
ext4 exposed as a node-pinned hostPath PV, so "disk full" is a real ENOSPC on the PD volume only. Harness:
`cluster/k3s_pd_disk_full.py` (1 Hz samples of PD `/v1/ready`, `/v1/health`, pod Ready, Server `/readiness`, one
schema write and one vertex read every 5 s through the Service); oracle 2 000 vertices / 6 000 edges.

## Pass 1 timeline (`samples.json`, `pd-disk-full.json`)

| t (s) | event | PD `/v1/ready` | PD pod | Server `/readiness` | writes / reads |
|---|---|---|---|---|---|
| 0-60 | baseline, disk 49 % used (raft log preallocation) | 200, `STATE_LEADER` | Ready | 200 `ok`, `pd_reachable=true` | 202 / 200 |
| 60 | `dd` fills the volume to 100 % (270 MB) | 200 for 27 more s | Ready | 200 | 202 |
| 87 | PD steps down | **503**, `{"ready":false,"state":"STATE_FOLLOWER","isLeader":false}` | NotReady from t=112 (3 × 10 s) | 200, `pd_reachable=false` from t=162 | writes **fail** (20 s client timeout), reads 200 |
| 241 | filler removed, disk back to 49 % | **still 503, still follower** for the whole 180 s | NotReady | 200, `pd_reachable=false` | writes fail, reads 200 |
| 422 | harness deletes the PD pod | up again at 451 as `STATE_LEADER` | Ready | 200, `pd_reachable=true` from 506 | 202 from t=456 |
| end | oracle check: 200 sampled vertices | 0 mismatches, 0 acknowledged writes lost | | | |

`/v1/health` answered 200 throughout (the chart's liveness probe), so nothing restarted the leaderless PD; only the
harness's pod delete did.

## What it means

- A single-node PD does not regain raft leadership after a transient disk-full episode, even once the disk is free
  again; the process stays a healthy-looking follower of a one-member group until restarted. This is the second
  independent reproduction of `docs/findings.md` "single-node PD that fails one periodic raft snapshot stays
  leaderless" (2026-09-15, VM, root disk at 98 %), now with a clean trigger and a clean recovery path.
- Data plane during the episode: reads through the Server keep working (Store answers, the Server's known store list
  holds), schema writes fail. The Server `/readiness` from #3221 stays 200 with `pd_reachable=false`, as designed.
- Chart consequence: with `pd.replicas=1` a leaderless PD is dead, but liveness on `/v1/health` never restarts it.
  For the single preset `pd.livenessPath: /v1/ready` (or a liveness that fails after N minutes of `ready=false`)
  would self-heal exactly this case; for 3 PD the current `/v1/health` liveness remains right.

Pass 2 (`pass2/`) repeats the fill with the PD log and `/v1/members` captured at every step, since pass 1 deleted the
pod before saving its log.
