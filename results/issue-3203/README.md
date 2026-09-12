# apache/hugegraph#3203 — Server exits 1 on a cold start, reproduced outside Kubernetes (2026-09-12)

Lab of [docs/setup.md](../../docs/setup.md): 1 PD + 3 Store + 1 Server, tarball built from master `60c8803`, no
Docker. `cluster/repro_coldstart.sh <delay> server` wipes PD/stores/server, starts PD, starts the Server one second
later and starts the stores `<delay>` seconds after PD is listening (`X2=<s>` delays the second and third store
separately). One summary per run: store registration times (PD log), every `error code = 105/102`, the retry sleeps,
the `upper limit` line, the Server's exit code and whether REST came up.

| Server config | stores register (after PD) | result | log |
|---|---|---|---|
| `usePD=true`, first boot of the cluster | 1 store at +5 s, two at +71 s | 10 attempts in 38 s (sleeps 1,1,1,2,3,4,5,6,7,8), `upper limit : 10`, **exit 1** | `usepd-x5-x70.log` |
| `usePD=true`, first boot of the cluster | 1 store at +5 s, two at +26 s | 9 attempts, the 9th succeeds, Server starts | `usepd-x5-x25.log`, `server-usepd-x5-x25.log` |
| local `conf/graphs`, no `usePD` | all at +20 / +45 / +60 s | Server up after 8 s, no 105 at all; the missing stores show only as code 102 in `task-db-worker` | `x20-server.log`, `x45-server.log`, `x60-server.log` |

The partition lookup on the main thread is the creation of the system graph `DEFAULT-~sys_graph`:
`GraphManager.loadMetaFromPD → kvStoreInit → createSysGraphIfNeed` calls `createGraph(..., init = true)` only when PD
holds no system-graph config yet, and `initBackend()` on hstore is `HstoreStore.init → createTable`. Hence first boot
only, never on pod replacement. `init-store` is not involved (it skips hstore graphs since `b9a3dd9`, `x60-init.log`).

Side finding, not part of the issue: with a comma-decimal JVM locale (`pl_PL` here) `usePD=true` fails before any of
this with `Can't write json: For input string: "0,00"` from `GraphSpace.info()`
(`Float.parseFloat(String.format("%.2f", ...))`); the runs above use `-Duser.language=en -Duser.country=US`.
