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

## Fix, 2026-09-16: wait for the stores at startup (PR apache/hugegraph#3210, `fix/server-wait-for-stores`, `0084e77d` on master `1a15e762`)

`GraphManager` polls PD for the active store count before any hstore graph is opened, bounded by the new server
option `pd.stores_wait_timeout` (seconds, default 300, 0 = old behaviour), logging progress every 5 s and naming the
option in the timeout error. The required count comes from PD's `getPDConfig()`: `min_store_count` when PD sends it,
otherwise `shard_count`. PD's `ConfigService` builds the served config from `partition_count` and `shard_count` only
(`ConfigService.java:82-83`), so on the wire `min_store_count` is 0 and the fallback is what actually applies (both
are 3 on the lab). Same reproduction as above (`cluster/repro_coldstart.sh`, `SV=<dist>` selects the server build,
stores at +5 s and +71 s after PD, `usePD=true`), logs in `fix/`:

| server build | stores register | result | log |
|---|---|---|---|
| master `1a15e762` | +5 s, +71 s, +71 s | 18 × `error code = 105`, sleeps 1,1,1,2,3,4,5,6,7,8, `upper limit : 10` at 23:16:13, **exit 1 after 38 s**, REST down | `fix/before-1a15-x5-x71.log` |
| `1a15e762` + fix | +5 s, +71 s, +71 s | `PD needs 3 active store(s) (min_store_count=0, shard_count=3); waiting up to 300s`, 14 progress lines (`Waiting for 0/3 … 1/3 … 3/3`), `3 active store(s) in PD after 70s`, **0 × 105, exit 0 after 81 s**, REST 200 | `fix/after-wait3203-x5-x71.log` |
| `1a15e762` + fix, `pd.stores_wait_timeout=20`, stores at +120 s | +120 s ×3 | 5 progress lines, then `Timed out after 20s waiting for 3 active store(s) in PD (0 registered); start the stores first or raise pd.stores_wait_timeout`, exit 1 after 31 s | `fix/after-wait3203-timeout20-x120.log` |

Unit test `GraphManagerStoresWaitTest` (3 cases: enough stores at once, stores arriving over four polls with one
failed query in between, timeout message naming the option) passes on JDK 11. The first version of the fix waited
inside `createSysGraphIfNeed()` (first boot only) and did nothing on the lab, because the local `conf/graphs` hstore
graph is opened before `loadMetaFromPD()` and already trips the 10-retry ceiling; the wait therefore sits in the
constructor, before any graph is opened, and applies to every start with `usePD=true`.
