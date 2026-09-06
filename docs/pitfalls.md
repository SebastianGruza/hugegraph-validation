# Pitfalls

Every entry cost real time. Format: symptom → cause → what to do.

## Configuration

### `pdserver.address` is silently ignored by the store
**Symptom:** stores register with `localhost:8686` although `application.yml` names the PD host.
**Cause:** the store bundles `application-pd.yml` on the classpath and activates it through
`spring.profiles.include: pd`; profile-specific files override the base file.
**Fix:** put an external `conf/application-pd.yml` with `pdserver.address` next to `application.yml`.

### Three JDKs on one box
**Symptom:** build fails on JDK 21 (Lombok, minicluster module); server fails on JDK 17
(gremlin-groovy 2.5); PD/store are fine on 17.
**Fix:** build/PD/store on Temurin 17, server on Temurin 11, and export `JAVA_HOME`/`PATH`
explicitly in every start script — never rely on the login shell's default.

### `install-dist` breaks the build
**Symptom:** `mvn package` fails in the antrun step of `install-dist`.
**Fix:** `-pl '!install-dist'`; nothing downstream needs that module.

### Stale `bin/pid` and zombie JVMs
**Symptom:** `start-*.sh` says "already running" although nothing listens; or `stop-*.sh`
returns and the JVM is still there.
**Cause:** the dist scripts keep the PID in `bin/pid` and trust it; a hung JVM keeps the file valid.
**Fix:** kill by the **port owner's PID** (`ss -tlnp`), then `rm bin/pid`. `node_store.sh` /
`node_servers.sh` do this in a loop until the ports are free.

### Wiping the cluster is all-or-nothing
**Symptom:** after wiping PD (and only PD) the hstore server answers reads but every write hangs;
store logs say `The store ID ... does not match the PD ... delete the store ID` (PD error 101);
PD says `The number of active stores is less then 3` (error 105); `init-store` and the suite spin
with `Waiting 7 seconds for the next try`.
**Cause:** each store persists its store id in `storage/hgstore-metadata`; a fresh PD does not know
it and rejects the node. The server keeps retrying forever.
**Fix:** wipe `storage/` on **every** store node together with `pd_data` on the PD node
(`cluster/run_cycle.sh` → `node_store.sh stop`). Our older single-node driver only wiped the PD
node and worked only because the cluster used to have one store.

### Stores must start after the PD leader exists
**Symptom:** `PD unreachable, pd.peers=...:8686` → `Failed to get the PD leader` → store shuts
down (`Unable to start embedded Jetty server ... InterruptedException`), although `:8686` is open.
**Cause:** the port is bound before raft has elected a leader; the store's registration retries are
exhausted within seconds and it exits instead of waiting.
**Fix:** wait ≥ 10 s after PD listens, then start the stores; verify `:8500` on each node and
retry the start once (`node_store.sh start`).

### Two servers on one PD: three things the shipped config does not do
- `rest-server.properties` has **no `gremlinserver.url`**; `start-hugegraph.sh` then assumes gremlin port 8182,
  and a second server on the same host fails with `The port 8182 has already been used` even though its
  `gremlin-server.yaml` says otherwise (the yaml ships with `#port: 8182` commented out too). Set both
  `gremlinserver.url` and the yaml `port:` explicitly (`cluster/make_dist_pair.sh`).
- **`usePD` defaults to `false`.** Without `usePD=true` (+ server-level `pd.peers`, `server.urls_to_pd`) the
  server uses PD only as the HStore backend; `GraphManager.loadMetaFromPD()` never runs, so no graph
  add/remove/update/clear listeners are registered and graphs created on one server are invisible to the
  other. The only PD KV watch such a server holds is the schema-cache-clear one of each open graph.
- With `usePD=true` on a **non-English locale JVM the server does not start** — `NumberFormatException:
  For input string: "0,00"` from `GraphSpace.info()` ([F10](findings.md#f10)). `export LC_ALL=C.UTF-8` before
  the start script.

### Graph creation is compensated by lazy loading — graph removal is not
In PD-meta mode `GraphManager.graph(space, name)` loads an unknown graph from PD meta on first access, so
"server B sees a graph created on server A" proves nothing about B's watch. `GET …/graphs` even lists
graphs straight from PD meta. To test the KV watch use **removal**: A drops a graph B has already
constructed; B keeps serving it (stale HTTP 200) until the `GRAPH/REMOVE` event arrives
(`cluster/pd_watch_exp2.sh`). The add-side signal is the `Accept graph add signal from etcd for …`
log line, not the REST result.

### A server that answers is not a server that works
**Symptom:** `GET /graphs/hugegraph` returns `{"backend":"hstore"}` while every insert hangs.
**Fix:** smoke-test a **write** (`node_servers.sh start` posts a property key) before trusting the
cluster.

## HTTP / REST

### Responses are gzipped regardless of `Accept-Encoding`
**Symptom:** `json.loads` fails on binary garbage; `curl` without `--compressed` prints noise.
**Fix:** check the magic bytes (`1f 8b`) and `gzip.decompress` — `HG.http()` in the suite does.

### Typed ids in REST
- Edge creation with a JSON body does **not** parse typed ids (`U"..."` for UUID vertices);
  `EdgeAPI.getVertex` takes them as strings. Create such edges through Gremlin (`addE`).
- The query parameter `vertex_id=U"550e8400-..."` **does** work for edge listing.
- `PRIMARY_KEY` vertices get ids of the form `<vertexLabelId>:<pk>` (`4:alice`); the label id
  depends on creation order, so look the id up instead of hard-coding it (`run_S` does).

### String predicates are rejected by the REST `properties` filter
`properties={"asset":"P.gte(\"ETC\")"}` → `Invalid value 'ETC', expect a number`;
`P.between("ETC","ETC!")` → `expect a list of number`. The same range through Gremlin
(`has('asset', gte('ETC'))`) works on both backends. REST parser limitation, not a backend one —
recorded as [F2](findings.md#f2). The suite keeps the REST cases so the class stays `BOTH-ERR`
(a change to `TARGET-ERR` or `OK` would be news).

### Paging tests need a limit that is a multiple of 500
The REST default `limit=100` and the core tests' `limit(1)` never cross an internal batch
boundary; [F1](findings.md#f1) only shows at `limit` = 500, 1000, ... Use several page sizes.

## Gremlin and data semantics

Things that looked like bugs and were suite expectations — the oracle overruled us every time:

- A Gremlin script that `graph.addVertex(...)`s and then queries **sees the new vertices before
  `graph.tx().commit()`**; the delta *after − before* is 0 and that is correct. Compare against a
  baseline taken before the script instead.
- `Text.contains(...)` is available in the gremlin-groovy sandbox without an import.
- Sets that are compared must be sorted deterministically: `order().by(id)` as the tie-break, or
  hash the sorted ids (the suite does the latter).
- A dataset generated with `18 + (i*7) % 63` had no `age=30`; the "exact value with ties" case must
  use a value that exists (we use 32).
- Gremlin string ranges order by **UTF-16 code units** (Java `String.compareTo`), not UTF-8 bytes:
  `gte('～')` (U+FF5E) excludes `😀` (surrogate D83D...), `gte('ÉTC')` includes both. Both
  backends agree; only the comments of the legacy bash suite assumed byte order.

## Maven on the server node

- `-pl hugegraph-server/hugegraph-test` **without** `-am` fails offline with
  `org.apache.hugegraph:hugegraph:pom:${revision} (absent)`: installed poms keep the unflattened
  parent version. Build from the reactor with `-am`; `-Drevision=1.7.0` does not help.
- RAT fails (`Too many files with unapproved license`) because the unpacked distributions and their
  logs live inside the clone: `-Drat.skip=true` (and `-Dcheckstyle.skip=true`).
- The first `test` run needs network for `surefire-junit4`; drop `-o` for it.
- `hugegraph-test/src/main/resources/hugegraph.properties` sets `query.page_size=2` and
  `query.batch_size=4`: index sub-queries are chunked by 2, so a label-index paging test cannot
  reproduce F1 there (only the edge test can).
- `git fetch` over https from an unauthenticated workstation may hit GitHub's rate limit
  (`temporarily limiting some unauthenticated downloads`); use an authenticated remote.

## Shell / orchestration

### `pkill -f` matches the shell that runs it
`ssh host 'pkill -f "hg_suite.py"; ...'` kills the remote `bash -c` whose command line contains
the pattern — including when the pattern only appears **elsewhere in the same command**
(`pkill -f "hg_suite.p[y]"; python3 -m py_compile hg_suite.py` still dies). Rules: bracket trick
(`patter[n]`), never mention the plain string anywhere in the same command line, and prefer killing
by port owner PID. Also watch heredoc contents, `PATH` strings and `rm` paths (`pd_data` matched
`apache-hugegraph-p[d]` once).

### Multi-line ssh commands with escaped `$`
A long inline command with `\$(...)` substitutions returned ssh exit 255 with no output. Put
per-node logic in a script, `scp` it, run `bash script`. Use `ssh -n` in loops so ssh does not eat
the loop's stdin.

### Python output vanishes in redirected logs
`print()` is block-buffered when stdout is a file; a hung run shows an empty log. Run `python3 -u`
(the suite flushes case lines itself, but not load-phase output).

### `git apply` twice
`validate_patch.sh red` is re-runnable: it applies the patch only if `git apply --check` passes,
then resets `hugegraph-core` so the new tests run against unpatched code.
