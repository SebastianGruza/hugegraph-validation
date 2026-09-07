# PR #2994 — condition resolution semantics for label queries

> Re-measured on the next head: [`fefe3ca`, 2026-09-07](fefe3ca/README.md) — finding 2 closed, `hasKey`/`hasValue` shapes added.

Measured 2026-09-06 on the lab in [docs/setup.md](../../docs/setup.md): hstore on PD + 3 store nodes
vs an embedded rocksdb oracle, both servers built from the same tree, Temurin 17 build / Temurin 11 runtime.
Everything below is reproducible from the files in this directory; the commands are at the end.

## Server versions

| Side | Tree | Notes |
|---|---|---|
| `master` | apache/hugegraph `origin/master` `36811483` | *fix(server): filter index results before input ordering (#3182)*, 2026-09-06 |
| `pr2994` | PR #2994 head `ac641c6d` | *fix(server): preserve safe lookup plans around label filters*; contains `36811483`, so `pr2994` = master + the PR and nothing else |
| `pr2994p3184` | `pr2994` + PR #3184 head `0ecc10a3` (merge `bb087ea8`) | only for the hstore backend axis: #3184 is the interim guard for the #3090 store-side decode crash on sort-key range scans |

`hugegraph-core` jar is byte-identical between `pr2994` and `pr2994p3184`; only `hugegraph-hstore` differs.

## 1. Backend axis — hstore vs rocksdb, same server code

`suite/hg_suite.py` (174 cases, sections S/L/J/K) and `suite/hg_j8.py` (155 cases: J8 review shapes,
P execution plans, G Gremlin `~page` paging) on a fresh graph per side.

| Side | Suite | J8 / P / G |
|---|---|---|
| `master` | OK=130, TARGET-ERR=38, BOTH-ERR=6 — J: 32 OK, 1 TARGET-ERR, 2 BOTH-ERR; S: 37 TARGET-ERR (#3090) | OK=139, TARGET-ERR=1 (#3090 shape), BOTH-ERR=15 |
| `pr2994` | OK=132, TARGET-ERR=38, BOTH-ERR=4 — J: 34 OK, 1 TARGET-ERR (#3090 shape) | OK=142, MISMATCH=6, TARGET-ERR=6, BOTH-ERR=1 (see §4) |
| `pr2994p3184` | **OK=170, BOTH-ERR=4, no MISMATCH / DUP / TARGET-ERR** — J: 35/35 | OK=142, MISMATCH=6, TARGET-ERR=6, BOTH-ERR=1 (identical to `pr2994`, see §4) |

The 4 `BOTH-ERR` are string range predicates in the REST `properties` filter, rejected by the REST layer on both
backends. Files: `compare_master.txt`, `compare_pr2994.txt`, `compare_pr2994p3184.txt`, `j8_compare_*.txt`.

Attribution: the only difference between `pr2994` and `pr2994p3184` on hstore is the #3090 shape
(`j8_compare_hstore_pr2994_vs_pr2994p3184.txt`: exactly one case, `E(a->b flow ETC) via outE limit neq(dummy1)`).
Every label-semantics result below is therefore attributable to #2994 alone.

## 2. Version axis — rocksdb master vs rocksdb pr2994

Backend held constant, so any difference is the server change itself.

**Suite (174 cases):** `OK=168, ORACLE-ERR=2, BOTH-ERR=4` — the two cases where master throws and the PR answers:

```
g.V().has('age',gte(30)).hasLabel(without('person','robot'))
  master: Can't do index query with [LABEL != 5, LABEL != 6] and [12 >= 30]        pr2994: 0 vertices
g.V().hasLabel(without('person')).barrier().has('age',gte(60))
  master: Don't accept query based on properties [age] that are not indexed ...     pr2994: 100 vertices
```

**J8 (93 cases): `OK=72, MISMATCH=18, ORACLE-ERR=2, BOTH-ERR=1`.** All 18 mismatches are the same defect on
master: `score` is range-indexed on `person` only; 60 of the 300 `robot` vertices have `score >= 40`.

| Query (Gremlin) | master | pr2994 |
|---|---|---|
| `g.V().has('score',gte(40)).hasLabel(neq('person'))` | 60 | 60 |
| same with `limit(100000)`, `skip(0)`, `range(0,100000)`, `aggregate('x')`, `coin(1.0)` or `barrier()` between the property and the label | **0** | 60 |
| `g.V().has('score',gte(40)).limit(100000).hasLabel(neq('person')).has('age',gte(30))` | **0** | 48 |
| `g.V().has('score',gte(40)).not(hasLabel('person'))`, also with `limit()` | **0** | 60 |
| `g.V().has('score',gte(40)).where(__.not(__.hasLabel('person')))`, also with `limit()` | **0** | 60 |
| `g.V().not(or(hasLabel('person'), has('age',lt(30)))).has('score',gte(40))` | **0** | 48 |
| `g.V('a').union(__.V().has('score',gte(40))).hasLabel(neq('person'))`, `flatMap`, `repeat(...).times(1)`, with `limit()`, with `where(not(hasLabel))` | **0** | 60 |
| `g.V().has('score',gte(40)).or(hasLabel(neq('person')), has('cnt',5))` | **180** | 240 |

Master pushes `score` into the `person` index and applies the label locally, so every robot is lost silently —
no error, wrong answer. With the barrier-like step removed master returns 60, i.e. master is inconsistent with
itself. The PR returns the complete set in every shape, identically on hstore.

The 2 `ORACLE-ERR` are master exceptions the PR turns into correct answers:
`g.V().has('cnt',5).limit(100000).hasLabel(neq('person'))` (master: *not indexed in any label*, PR: 0, correct — robots carry no `cnt`)
and `g.V().has('type',gte(2)).outE('deal').hasLabel(neq('flow')).inV()` (master: *Not supported querying edges by
[OWNER_VERTEX, DIRECTION, LABEL != 1, LABEL == 11]*, PR: 0, correct — no `deal` edge leaves a `type >= 2` firm).

**Paging (G, 42 cases):** master rejects every `~page` traversal that carries a downstream negative label
(`Invalid paging traversal`, 12 cases); the PR pages them and the union of all pages equals the unpaged set for
page sizes 7, 50 and 500, identically on both backends. The price is visible in the page counts: 501 pages of size 7
for 100 results, because filtered pages are empty while the cursor advances through the whole vertex table
(`j8_compare_version-axis_rocksdb_master_vs_pr2994.txt`, section G).

Files: `compare_version-axis_rocksdb_master_vs_pr2994.txt`, `j8_compare_version-axis_rocksdb_master_vs_pr2994.txt`,
`j8_master_oracle.json`, `j8_pr2994_oracle.json` (full id sets).

## 3. Plan and cost axis — the two review findings of 2026-09-06

Section P records the final traversal of `explain()` per query; `suite/scale_probe.py` loads 1 000 000 `big`
vertices (no property index) next to the suite data on rocksdb and times the shapes. The `big`/`mark` labels
carry no `age`/`score`/`fname` index, which is what any production schema looks like.

**Point lookups (finding 1) — fixed at `ac641c6`.** Plan and time are identical to master:

| Query | plan (both versions) | master | pr2994 |
|---|---|---|---|
| `g.V('big00000010').limit(10).hasLabel(neq('mark'))` | `[HugeGraphStep(vertex,[big00000010]), RangeGlobalStep(0,10), HasStep([~label.neq(mark)])]` | 3 ms | 8 ms |
| `g.V().hasId('big00000010').limit(10).hasLabel(neq('mark'))` | same | 3 ms | 10 ms |
| `g.V('big00000010','mark0').limit(10).hasLabel(neq('big'))` | same shape | 3 ms | 7 ms |

**Unsafe-label gate breadth (finding 2) — confirmed and measured.** On the 1 M graph (`scale_master.json`,
`scale_pr2994.json`):

| Query | master plan | master | pr2994 plan | pr2994 |
|---|---|---|---|---|
| `g.V().has('age',gte(60)).limit(100000).hasLabel(neq('person'))` | `HugeGraphStep(Vertex,[age.gte(60)])` | 8 ms, **incomplete** (see §2) | `HugeGraphStep(vertex,[])` + local `HasStep([age.gte(60)])` | **`Too many records(must <= 800000) for the query: Query * from VERTEX`** |
| `g.V().has('age',gte(60)).out().hasLabel(neq('person'))` | `HugeGraphStep(Vertex,[age.gte(60)]), HugeVertexStep(OUT,Vertex,[~label.neq(person)])` | 17 ms, correct | `HugeGraphStep(Vertex,[age.gte(60)])` in `explain()`, but the executed query is `Query * from VERTEX` | **`Too many records(...)`** |
| `g.V().has('age',gte(60)).out().where(__.not(__.hasLabel('person')))` | index lookup | 18 ms, correct | as above | **`Too many records(...)`** |
| `g.V().has('score',gte(40)).limit(100000).hasLabel(neq('person'))` | index lookup | 11 ms, incomplete | full scan | **`Too many records(...)`** |
| `g.V().has('fname',Text.contains('gold')).limit(100000).hasLabel(neq('person'))` | `fname.TEXT_CONTAINS(gold)` | 7 ms | `HasStep(lambda)` local | **`Too many records(...)`** |
| `g.V().has('age',gte(60)).hasLabel(neq('person'))` (no barrier) | full scan on **both** | `Too many records` | full scan | `Too many records` |

Rows 2 and 3 are regressions in the strict sense: the negative label applies to the `out()` vertices, never to the
`g.V()` candidates, master answers correctly in 17 ms, and the PR fails above `Query.DEFAULT_CAPACITY`. On the
small suite graph (only `person` and `robot`, both with an `age` index) the same shapes keep the pushdown
(`j8_pr2994_oracle.txt`, section P), which is why the PR's own tests do not see this: the gate depends on whether
*every* schema label has compatible index coverage.

Rows 1 and 4 are the deliberate trade-off the PR description records (complete results instead of a silently
incomplete 8 ms answer); the cost is that above 800 000 vertices the query fails outright instead of returning.

## 4. Not attributable to this PR (recorded so nobody re-investigates)

- **`~page` under the Gremlin sandbox on hstore.** The 6 `TARGET-ERR` + 6 `MISMATCH` in the `pr2994` backend axis
  (section G, the two positive-control shapes) are `Not allowed to access thread group via Gremlin`, thrown from
  `ExecutorPool$DefaultThreadFactory.newThread` inside `OrderedKvIterator.initialize` (`hg-store-client`), reached from
  `HstoreSessionsImpl.scanOrdered`. It fires on a fresh server for the first Gremlin `~page` range-index queries and
  stops after one REST paging call warms the pool. Master shows the same class of failure on a fresh server with
  different first-touch exceptions (`Not allowed to access system property(io.grpc.netty...useCustomAllocator)`,
  then `Not allowed to access thread via Gremlin`), so this is lazy initialisation in the store client meeting
  `HugeSecurityManager`, not a change in this PR. `scanOrdered` and `OrderedKvIterator` are unchanged by the PR.
- **Fresh master hstore server killed by its first Gremlin query.** If the first `ConditionQuery` in the JVM is built
  under a Gremlin request, `ConditionQuery.<clinit>` → `GsonBuilder.create()` → `gson.internal.JavaVersion` reads
  `java.version`, `HugeSecurityManager.checkPropertyAccess` throws, and the class stays `Could not initialize class
  org.apache.hugegraph.backend.query.ConditionQuery` until restart — REST included. Reproduced 2/2 on master hstore
  (`36811483`); not on master rocksdb (the class is initialised during startup there) and not on the PR build.
  Separate report.
- `g.V().has('score',gte(40))` returns 1800 on every version and backend: the 60 robots with `score >= 40` are missing
  because `robot` has no `score` index. Pre-existing, unchanged, out of scope here.

## Reproduce

```bash
# lab: docs/setup.md; worktrees ~/hg-master (origin/master), ~/hg-pr2994 (PR #2994 head), ~/hg-pr2994p3184
mvn -q -ntp -DskipTests -Drat.skip=true -Dcheckstyle.skip=true -Dmaven.javadoc.skip=true \
    -pl hugegraph-server/hugegraph-dist -am package                 # in each worktree
bash cluster/make_dists.sh ~/hg-<tag> <tag>                          # dist-<tag>-hstore (:8080), dist-<tag>-rocksdb (:8081)
for T in master pr2994 pr2994p3184; do cluster/run_side.sh $T; done # wipe, boot, load, suite, hg_j8, compare
python3 suite/hg_suite.py compare master_oracle.json pr2994_oracle.json --ids        # version axis
python3 suite/hg_suite.py compare j8_master_oracle.json j8_pr2994_oracle.json --ids
python3 suite/scale_probe.py --port 8081 --n 1000000 --out scale_<tag>.json          # 1 M vertices, timings + plans
```

Server versions and backends are recorded in the `meta` block of every JSON report.
