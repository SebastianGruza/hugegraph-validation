# Lab setup

Everything below is what actually runs the suite. Hostnames and addresses are the lab's; the
scripts take them from environment variables (see the header of `cluster/run_cycle.sh`).

## Topology

Three Proxmox VMs (Debian, user `seba`, key-based ssh from the workstation to all three; the
server node has **no** key to the store nodes, which is why orchestration runs from the workstation):

| Node | Address | Runs |
|---|---|---|
| `hugegraph-dev` | 192.168.80.235 | PD (`8686` gRPC, `8620` REST), store (`8500`/`8510`/`8520`), **hstore server** (`8080` REST, `8182` gremlin), **rocksdb oracle server** (`8081` REST, `8183` gremlin), maven + source clone |
| `hugegraph-node2` | 192.168.80.236 | store only |
| `hugegraph-node3` | 192.168.80.237 | store only |

The two store-only nodes are Proxmox clones of the first one (static IP set in
`/etc/network/interfaces.d/ens18`, hostname changed). Same home layout on all three.

## Toolchain

```
~/tools/jdk17            Temurin 17  — builds, PD, store
~/tools/jdk11            Temurin 11  — server runtime (gremlin-groovy 2.5 fails on 17)
~/tools/apache-maven-3.9.9
```

Do not build on JDK 21: Lombok in the minicluster module breaks. PD and store run on 17, the
server on 11 — three different `JAVA_HOME`s on one machine, always exported explicitly
(`cluster/node_servers.sh`, `cluster/node_store.sh`).

## Source and build

```
~/hugegraph                 clone of apache/hugegraph
  branch combined = master 98477f0 + PR #3184 + PR #3182 + PR #2994   (the server code under test)
```

The exact heads that make up `combined` (`9db8797b`), so the branch can be rebuilt bit for bit:

| Part | Commit | Notes |
|---|---|---|
| apache/hugegraph `master` | `98477f0f56` | *chore(docker): refactor docker-compose topologies with Hubble (#3149)*, 2026-08-31 |
| PR #3184 head | `1072872668` | = master + `847fab9d` + `10728726` (our interim guard for #3090); open |
| PR #3182 head | `acbee16753` | *fix(core): preserve internal record queries*; **merged into master on 2026-09-03** |
| PR #2994 head | `5cb9a51956` | *fix(ci): retry transient store check*; open |

```bash
git fetch origin 98477f0f56 pull/3184/head pull/3182/head pull/2994/head
git checkout -b combined 1072872668            # PR #3184 head already contains master 98477f0
git merge --no-edit acbee16753                  # PR #3182  -> 0abbfc78
git merge --no-edit 5cb9a51956                  # PR #2994  -> 9db8797b
```

No textual conflicts in either merge. The PR heads had not moved between the run (2026-09-03 09:49) and
the time of writing; since #3182 is merged, `combined` is equivalent to today's master + #2994 + #3184.

```bash
export JAVA_HOME=~/tools/jdk17 PATH=~/tools/jdk17/bin:~/tools/apache-maven-3.9.9/bin:$PATH
mvn -DskipTests package -pl '!install-dist'     # install-dist's antrun step fails; nothing needs it
```

The distributions are unpacked **inside** the clone (which trips RAT later — see pitfalls):

```
hugegraph-pd/apache-hugegraph-pd-1.7.0
hugegraph-store/apache-hugegraph-store-1.7.0
hugegraph-server/apache-hugegraph-server-1.7.0      hstore server
hugegraph-server/dist-rocksdb                        copy of the server dist, rocksdb config, other ports
```

The oracle is **not** a different build: `dist-rocksdb` is a copy of the same server distribution
with `backend=rocksdb` and shifted ports. Only the storage layer differs, which is the point.

### The `master` side — a second pair of server distributions

The PRs under test change `hugegraph-core`, `hugegraph-api` and `hugegraph-hstore` (server side only;
`git diff --stat master..combined -- hugegraph-pd hugegraph-store` is empty), so "before any patch" needs a
server built from master, while PD and the store nodes stay as they are. On the server node:

```bash
git -C ~/hugegraph worktree add ~/hg-master 98477f0f56
cd ~/hg-master && mvn -q -ntp -DskipTests -Drat.skip=true -Dcheckstyle.skip=true -Dmaven.javadoc.skip=true \
    -pl hugegraph-server/hugegraph-dist -am package          # ~3 min warm; produces hugegraph-server/apache-hugegraph-server-1.7.0
bash cluster/make_master_dists.sh    # -> dist-master-hstore (conf of the combined hstore dist, :8080) and dist-master-rocksdb (:8081)
cluster/run_cycle.sh master          # node_servers.sh gets HG_SV / HG_RD pointing at the master dists
```

`run_cycle.sh fix` is the historical variant that only swaps `hugegraph-hstore-1.7.0.jar` in the combined
dist for the PR #3184 build (`/tmp/hstore-fix.jar`); it was used for the legacy suite before the
`master` worktree existed.

## Configuration that mattered

### PD — `hugegraph-pd/.../conf/application.yml`

```yaml
grpc:
  host: 192.168.80.235
  port: 8686
pd:
  initial-store-count: 3
  initial-store-list: 192.168.80.235:8500,192.168.80.236:8500,192.168.80.237:8500
```

### Store — `hugegraph-store/.../conf/application.yml` (per node)

```yaml
pdserver:
  address: 192.168.80.235:8686
grpc:
  host: 192.168.80.236          # this node's address
  port: 8500
raft:
  address: 192.168.80.236:8510  # this node's address
server:
  port: 8520
app:
  data-path: ./storage          # holds db/, raft/ AND hgstore-metadata/ (the persisted store id)
```

**and** an external `conf/application-pd.yml` next to it:

```yaml
pdserver:
  address: 192.168.80.235:8686
```

The store's Spring config has `spring.profiles.include: pd`, so the `application-pd.yml` bundled
**on the classpath** overrides `pdserver.address` from the external `application.yml`. Without the
external `application-pd.yml` every store silently talks to `localhost:8686`.

### hstore server — `hugegraph-server/apache-hugegraph-server-1.7.0/conf`

```
rest-server.properties:      restserver.url=http://127.0.0.1:8080   gremlinserver.url=127.0.0.1:8182
gremlin-server.yaml:         port: 8182
graphs/hugegraph.properties: backend=hstore  serializer=binary  store=hugegraph  pd.peers=192.168.80.235:8686
```

### rocksdb oracle — `hugegraph-server/dist-rocksdb/conf`

```
rest-server.properties:      restserver.url=http://127.0.0.1:8081   gremlinserver.url=127.0.0.1:8183
gremlin-server.yaml:         port: 8183
graphs/hugegraph.properties: backend=rocksdb  serializer=binary  store=hugegraph
                             rocksdb.data_path=./rocksdb-data  rocksdb.wal_path=./rocksdb-data
```

## Start order

1. PD on the server node (`bin/start-hugegraph-pd.sh`, JDK 17); wait for `:8686` **plus ~10 s**
   for the raft leader.
2. Stores on all three nodes (`bin/start-hugegraph-store.sh`, JDK 17); each must show `:8500`
   listening. A store that reaches PD before the leader exists exhausts its retries and exits.
3. `bin/init-store.sh` then `bin/start-hugegraph.sh` for the hstore server (JDK 11). `init-store`
   fails with PD error 105 (`active stores is less then 3`) until all stores are registered —
   `node_servers.sh start` retries it.
4. `init-store.sh` + `start-hugegraph.sh` for the rocksdb dist (JDK 11).
5. Smoke test a **write** on the hstore server; a server that answers reads can still hang every
   write when PD has not assigned partitions.

`cluster/run_cycle.sh` does all of this, including the cluster-wide wipe that has to precede it.

## Running the suite

```bash
scp suite/hg_suite.py suite/page_probe.py seba@192.168.80.235:~/
ssh seba@192.168.80.235 'python3 -u hg_suite.py run --port 8081 --load --out validation/oracle.json'
ssh seba@192.168.80.235 'python3 -u hg_suite.py run --port 8080 --load --out validation/hstore.json --expect validation/oracle.json'
```

`--load` builds schema and data for the selected sections on a **fresh** graph; running it twice
on the same graph is not idempotent (batch inserts of existing ids error, index rebuilds queue).
Without `--load` the suite is read-only and repeatable (section K adds three vertices in a
transaction and drops them again).

## Running HugeGraph's own core tests against a patch

On the server node, from the clone (see `cluster/validate_patch.sh`):

```bash
mvn -q -ntp -Drat.skip=true -Dcheckstyle.skip=true \
    -pl hugegraph-server/hugegraph-test -am -P core-test,rocksdb \
    -Dtest='EdgeCoreTest#testQueryOutEdgesOfVertexInPagingAtBatchBoundary' \
    -DfailIfNoTests=false -Dsurefire.failIfNoSpecifiedTests=false test
```

About 40 s once the reactor is warm. `-am` is mandatory (see pitfalls), the first run needs
network for the surefire provider, and the core-test profile runs with `query.page_size=2`.
