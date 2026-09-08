# hugegraph-oracle-suite

Black-box, **oracle-mode** regression suite for [Apache HugeGraph](https://github.com/apache/hugegraph) 1.7.0.

The same set of REST and Gremlin queries is run against two live servers built from the **same
source tree**: the backend under test (`hstore` — PD + 3 store nodes) and a reference backend
(`rocksdb`, embedded). Results are compared as **sets of element ids** (count, distinct count,
md5 of the sorted ids, and the ids themselves), never as bare counts. Nothing is hard-coded as an
expected value: **the expectation is whatever the reference backend returns.**

This is an independent effort, not an Apache project. The lab, the suite and the findings are
maintained by [Sebastian Gruza](https://gruzalab.pl) (graph databases, distributed systems, blockchain
analytics), who is evaluating HugeGraph with the HStore backend for large-scale risk-propagation
graph workloads. The repository accompanies a working clone of `apache/hugegraph`: it holds the
suite, the cluster orchestration used to run it, the reports it produced, the bugs it found and the
patches written for them. The upstream reporting status of every finding is tracked in
[docs/findings.md](docs/findings.md).

## Why an oracle instead of expected counts

`hstore` pushes query conditions down into the store (`HstoreTable.prepareConditionQuery`) and
pages through gRPC scans; `rocksdb` filters in the server. Counting tests (`expect 1212`) pass
while the backend returns the *wrong* 1212 rows, returns duplicates, or returns them in an order
that breaks `limit`/`range`/paging. Comparing id sets against a backend that does not push anything
down catches all of that with zero hand-maintained expectations — and every time the suite disagreed
with our own expectations it was the expectation that was wrong (four times so far, see
[docs/pitfalls.md](docs/pitfalls.md#gremlin-and-data-semantics)).

The price: both servers run the same server code, so a semantic regression *in the server*
(e.g. a changed meaning of `hasLabel(neq(...))`) is invisible to the oracle. That axis is covered
by running the suite on two server versions (master vs. a PR branch) and diffing the JSON reports.

## Layout

```
suite/hg_suite.py            the suite: run / compare (Python 3, stdlib only)
suite/page_probe.py          paging probe: which ids duplicate, on which page boundary
suite/hg_j8.py               PR #2994 review shapes: J8 id sets, P execution plans (explain()), G Gremlin ~page paging
suite/scale_probe.py         1 M-vertex load + timings and plans for the point-lookup / index-pushdown shapes
suite/scale_fallback.py      the documented local-filter fallback and ~page sweep timed at 1 M vertices, per backend
cluster/run_cycle.sh         full cycle from the workstation: wipe 3-node cluster -> boot -> --load on both -> compare
cluster/run_side.sh          same cycle for any tag: dists from ~/hg-<tag>/hugegraph-server/dist-<tag>-{hstore,rocksdb}, plus hg_j8.py
cluster/make_dists.sh        dist-<tag>-hstore / dist-<tag>-rocksdb from a built worktree (conf carried over)
cluster/node_store.sh        per store node: stop+wipe / start (with PD-readiness retry)
cluster/node_servers.sh      on the server node: stop+wipe / init+start hstore and rocksdb servers
cluster/validate_patch.sh    red -> green -> deploy -> revert cycle for a core patch (maven on the server node)
cluster/make_master_dists.sh assemble master-built server distributions for the "before any patch" side
cluster/make_dist_pair.sh    two hstore servers (A :8080, B :8082) from one build, for cross-server PD-meta tests
cluster/pd_watch_exp2.sh     PD KV watch recovery experiment (issue #3152 / PR #3157): create -> drop on A, watch REMOVE on B across PD outages
cluster/legacy/              the original bash suite (#3090 sort-key reproducer with expected counts)
docs/setup.md                lab topology, builds, JDKs, every config file that mattered
docs/pitfalls.md             configuration and operational traps, with symptom -> cause -> fix
docs/findings.md             bugs and observations, evidence, root causes, reporting status
docs/results.md              result matrix and how to read the reports
patches/                     fixes as git format-patch against apache/hugegraph master
results/                     JSON/text reports of the 2026-09-03 runs + results/README.md (file provenance)
reports/<pr>/                one directory per upstream PR: README with the tables posted upstream + every raw report
```

## Quick start

Two servers up (see [docs/setup.md](docs/setup.md)); the suite needs only Python 3.

```bash
# reference backend first: builds schema + data (--load), writes the oracle report
python3 suite/hg_suite.py run --port 8081 --load --out oracle.json

# backend under test: same load, every case classified inline against the oracle
python3 suite/hg_suite.py run --port 8080 --load --out hstore.json --expect oracle.json

# or compare two reports afterwards; --ids prints the differing ids
python3 suite/hg_suite.py compare oracle.json hstore.json --ids
```

Full cycle on the 3-node lab (wipe everything, boot in the right order, load, run, compare):

```bash
cluster/run_cycle.sh combined        # ~3 minutes; reports land in ~/validation on the server node
```

## Sections

Sections map to the upstream issues/PRs whose territory they cover. Order matters: J uses the
data of S and L, so `--load` always loads in S, L, J, K order regardless of `--sections`.

| Section | Territory | Data | What it stresses |
|---|---|---|---|
| **S** sort keys / pushdown | #3090, PR #3184 | 1 222 edges `a -> b` with sort keys `(asset TEXT, epoch LONG)`, 4 owner-id strategies, every property type | prefix vs range on string and long sort keys, LongEncoding boundaries, UTF-8/UTF-16 ordering, `within`, IN/BOTH direction, owner-vertex equality with string/number/uuid/primary-key ids, 2-digit label ids, paging |
| **L** range index / paging | #3140 | 3 000 `person` vertices, RANGE_INT/FLOAT/LONG indexes, many ties | full sets, `limit` slices across partitions, `range(a,b)` tiling, page-cursor sweeps at 7 page sizes, `order().by()` |
| **J** label semantics | PR #2994 | + 120 `dummy1..3` edges, 300 `robot` vertices | `neq` / `without` / `within` / conflicting labels / `or` / `and` / `not`, negative labels across `barrier()` and `sideEffect()`, vertices with label + range index |
| **K** within x search x range | PR #3182 (merged 2026-09-03), #3180 | 200 `firm` vertices (range x2 + SEARCH index), 100 `deal` edges (sort key + range + search) | the #3180 combo, each component alone, every query twice (cache hit), 10x determinism, sort-key `within` on edges, uncommitted-transaction visibility |

### The combined-branch test asked for in #3090

The coordination note in apache/hugegraph#3090 (2026-09-03) asks for #2994, #3182, #3184 and the
already merged #3140 to be validated together, on four groups of cases. That is what this suite
ran, on `combined` = master `98477f0` + #3184 `1072872` + #3182 `acbee167` + #2994 `5cb9a519`
(recipe in [docs/setup.md](docs/setup.md#source-and-build)), hstore on PD + 3 stores against rocksdb:

| Requested | Section | Cases | master `98477f0` (before) | `combined` (after) |
|---|---|---|---|---|
| 1. #3184's sort-key prefix/range reproducer cases | **S** | 56 | 15 OK, **37 `TARGET-ERR`** (store-side decode crash, #3090), 4 `BOTH-ERR` | 52 identical sets, 4 `BOTH-ERR` (REST string predicates, same on both backends) |
| 2. #2994's single/multi/conflicting/`IN`/non-equality label cases, with edge sort keys | **J** | 35 | 32 OK, 1 `TARGET-ERR` (#3090 shape), **2 `BOTH-ERR`** (`without()` + range index throws on the server) | 35 identical sets |
| 3. #3182's flattened `within()` and post-filtering cases, cache hits and misses | **K** | 40 | 24 OK, **16 `MISMATCH`** (hstore returns only the first `within()` value when a search condition is present — wrong answers, no error) | 40 identical (every query run twice; #3180 combo ×10 stable) |
| 4. #3140's cross-partition limit, offset and page-cursor ordering | **L** | 43 | 43 identical sets | 43 identical sets across 3 store partitions |

Version axis, backend held constant: rocksdb master vs rocksdb combined differ in exactly the two
`without()` cases (master throws, combined answers) and nowhere else — `OK=168`. Details and raw reports:
[results/README.md](results/README.md); the before/after of each PR as findings F5, F8, F9 in
[docs/findings.md](docs/findings.md).

## Classes

| Class | Meaning |
|---|---|
| `OK` | identical id set |
| `DUP` | the backend under test returns duplicates the oracle does not (`uniq < n`) |
| `MISMATCH` | different set; the report shows `only-oracle` / `only-target` counts and ids |
| `TARGET-ERR` | only the backend under test throws — a backend bug |
| `ORACLE-ERR` | only the reference throws |
| `BOTH-ERR` | both throw — a server or suite limitation, not a backend bug |
| `NEW` / `MISSING` | case present in only one report (suite version drift) |

## Reports per PR

| PR | Report | Verdict measured |
|---|---|---|
| [apache/hugegraph#2994](https://github.com/apache/hugegraph/pull/2994) | [reports/pr-2994](reports/pr-2994/README.md) (2026-09-06, head `ac641c6`) [fefe3ca](reports/pr-2994/fefe3ca/README.md) (2026-09-07) and [e32a75f](reports/pr-2994/e32a75f/README.md) (2026-09-08, fallback + paging at 1 M on HStore) | label semantics correct on both backends, fixes 18 silently-incomplete shapes plus 10 `hasKey`/`hasValue` shapes that master answers with an empty set; point lookups preserved; the `has(indexed).out().hasLabel(neq(..))` full scan of `ac641c6` is fixed at `fefe3ca`; connective `hasId(...)` next to a negative label is still a full scan |

## Status (2026-09-03)

Server code = `combined` (apache/hugegraph master `98477f0` + PR #3184 `1072872` + PR #3182 `acbee167`
+ PR #2994 `5cb9a519`; #3182 has since been merged, so this equals master + #2994 + #3184),
hstore on a PD + 3-store cluster vs rocksdb: **174 cases, OK=170, BOTH-ERR=4**, no `MISMATCH`,
no `DUP`, no `TARGET-ERR`. The four `BOTH-ERR` are string range predicates in the REST `properties`
filter, which the REST layer rejects on both backends while Gremlin accepts them.

The suite found one server-level bug that counting could never see — a duplicated record on every
page boundary when the page limit is a multiple of 500 — root-caused, fixed and validated red/green
in [docs/findings.md](docs/findings.md#f1) with the patch in [patches/](patches/); reported as
[apache/hugegraph#3191](https://github.com/apache/hugegraph/issues/3191).

## License

Apache License 2.0 — the same license as HugeGraph, so patches and tests can move upstream as they are.
