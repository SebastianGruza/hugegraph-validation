# hugegraph-validation

An independent validation lab for [Apache HugeGraph](https://github.com/apache/hugegraph): the server,
the HStore/PD distributed backend, the Helm chart and the client toolchain. The method is always the
same: reproduce on a real cluster, measure against a reference, fix upstream, measure again.

This is not an Apache project. The lab, the harnesses and the findings are maintained by
[Sebastian Gruza](https://gruzalab.pl) (graph databases, distributed systems, blockchain analytics),
who evaluates HugeGraph with HStore for large-scale risk-propagation graph workloads. The repository
holds the suites, the cluster orchestration, the raw reports, the bugs found and the patches written
for them. The upstream status of every finding is tracked in [docs/findings.md](docs/findings.md).

## How the work is done

- **Oracle, not expectations.** The same REST and Gremlin queries run against two servers built from the
  same source tree, HStore (PD + 3 store nodes) and embedded RocksDB. Results are compared as sets of
  element ids. Whatever RocksDB returns is the expectation; nothing is hand-coded. The same idea now
  drives the client contract fixtures for the Rust roadmap: a recording from the reference client
  and the reference backend is the contract, and a backend regression shows up as a diff.
- **Real clusters, real failures.** Two labs: three VMs running bare processes (server versions side by
  side, jar swaps without reloading data, RocksDB oracle) and a three-node k3s cluster running the Helm
  chart as users will get it (pod kills, disk full, OOM limits, rolling upgrades under load, probes).
  Every run records what was tested: image digest or jar checksum, chart commit, configuration.
- **A finding is a reproducer plus a measurement.** Each upstream issue comes with a script that
  reproduces it, the logs, and the before/after numbers of the proposed fix, measured on the lab.
  Each PR carries tests that the upstream CI actually counts and answers review rounds with
  measurements rather than opinions.
- **Security goes through the ASF private channels**, never through public issues.

## Upstream contributions

Merged in `apache/hugegraph` master:

| PR | What | Merged |
|---|---|---|
| [#3184](https://github.com/apache/hugegraph/pull/3184) | HStore: sort-key prefix/range queries crashed the store-side decoder; sysprop-only range queries are no longer pushed down (part of #3090) | 2026-09-16 |
| [#3204](https://github.com/apache/hugegraph/pull/3204) | store-client: a stalled store held REST workers for minutes; commit retries now honour interrupts (F15, [#3199](https://github.com/apache/hugegraph/issues/3199)) | 2026-09-16 |
| [#3207](https://github.com/apache/hugegraph/pull/3207) | paging returned the page-boundary record twice when the limit was a multiple of 500, on RocksDB and HStore (F1, [#3191](https://github.com/apache/hugegraph/issues/3191)) | 2026-09-16 |
| [#3210](https://github.com/apache/hugegraph/pull/3210) | server: wait for the stores at startup instead of exiting on a cold start (F10) | 2026-09-17 |
| [#3216](https://github.com/apache/hugegraph/pull/3216) | `usePD=true` broke on JVMs with a decimal-comma locale ([#3215](https://github.com/apache/hugegraph/issues/3215)) | 2026-09-17 |
| [#3220](https://github.com/apache/hugegraph/pull/3220) | a server upgraded from 1.7.0 with `usePD=true` read an empty schema: meta cluster prefix bound before any graph opens ([#3219](https://github.com/apache/hugegraph/issues/3219), `results/upgrade-170-to-master`) | 2026-09-20 |

Open, under review:

| PR | What | Evidence here |
|---|---|---|
| [apache/hugegraph#3209](https://github.com/apache/hugegraph/pull/3209) | `DECIMAL` (BigDecimal) property data type: exact amounts through batch `update_strategies: SUM` ([#3206](https://github.com/apache/hugegraph/issues/3206)) | [docs/decimal-datatype.md](docs/decimal-datatype.md), `results/decimal` |
| [apache/hugegraph-toolchain#771](https://github.com/apache/hugegraph-toolchain/pull/771) | the same type in hugegraph-client, loader, spark connector and Hubble, E2E against the server branch | `results/decimal/client-e2e` |
| [apache/hugegraph#3221](https://github.com/apache/hugegraph/pull/3221) | storage-aware `GET /readiness` for the server ([#3212](https://github.com/apache/hugegraph/issues/3212)), with the chart side in [hugegraph/hugegraph#229](https://github.com/hugegraph/hugegraph/pull/229) | [docs/server-readiness.md](docs/server-readiness.md), `results/issue-3212` |

Issues filed from this lab, still open: [#3222](https://github.com/apache/hugegraph/issues/3222)
(a single-node PD never recovers leadership after a failed snapshot on a full disk; probes hide it;
`results/pd-disk-full-single`), [#3090](https://github.com/apache/hugegraph/issues/3090) analysis of
the server/store property codec mismatch (`results/issue-3090`).

Helm chart campaign ([apache/hugegraph#3132](https://github.com/apache/hugegraph/issues/3132), review in
[hugegraph/hugegraph#221](https://github.com/hugegraph/hugegraph/pull/221)): fault battery, cold start,
#3164 on pods, store memory under the cluster preset, PD disk full, rolling upgrade under load; see
[docs/helm-chart-faults.md](docs/helm-chart-faults.md), [docs/store-memory-preset.md](docs/store-memory-preset.md)
and `results/{helm-chart,issue-3164-pods,store-oom-cluster-preset,pd-disk-full-single,rolling-upgrade}`.

In progress, the Rust modernization roadmap groundwork ([toolchain#748](https://github.com/apache/hugegraph-toolchain/issues/748),
[server#3110](https://github.com/apache/hugegraph/issues/3110)): language-neutral client contract fixtures
recorded from the Java client with a Go runner and a loader baseline benchmark
([toolchain#772](https://github.com/apache/hugegraph-toolchain/issues/772)), and OLTP/storage workload
baselines plus rolling-upgrade and rollback requirements for HStore and PD
([server#3223](https://github.com/apache/hugegraph/issues/3223)). No Rust code is promised; the
deliverable is the reference every port will be measured against.

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
suite/                  the oracle suite (Python 3, stdlib only): hg_suite.py run/compare, page_probe.py,
                        hg_j8.py (PR #2994 shapes), scale_probe.py / scale_fallback.py (1 M-vertex timings)
cluster/                harnesses for the bare-process lab: run_cycle.sh / run_side.sh (wipe, boot, load,
                        compare), make_dists.sh, node_*.sh, validate_patch.sh (red -> green -> deploy -> revert),
                        repro_*.py (F15/F16 stalled-store reproducers), pd_watch_exp2.sh (#3152/#3157),
                        idx_bench.py + idx_*.sh (edge index write cost), sk_shapes.py, decimal_e2e.py,
                        race_*.sh (#3164 snapshot/compaction race), rebuild_integration.sh
cluster/k3s_*.py        harnesses for the Helm-chart k3s cluster: faults, readiness, #3164 on pods, pad load,
                        PD disk full, rolling upgrade under load
docs/                   setup.md (lab topology, builds, configs), pitfalls.md (symptom -> cause -> fix),
                        findings.md (F1..F18: evidence, root cause, upstream status), results.md,
                        decimal-datatype.md, server-readiness.md, helm-chart-faults.md, store-memory-preset.md
results/                raw reports per run or per issue (each directory with its own README), e.g.
                        pr-3184-sortkeys, issue-3090, upgrade-170-to-master, issue-3212, issue-3164-pods,
                        store-oom-cluster-preset, pd-disk-full-single, rolling-upgrade, decimal
reports/                per upstream PR: the tables posted upstream plus every raw report (pr-2994, index-cost)
patches/                fixes as git format-patch against apache/hugegraph master
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

## Status (2026-09-20)

Oracle suite on the bare lab, hstore (PD + 3 stores) vs rocksdb, server = apache master with the merged
fixes above: 174 cases, `OK=170`, `BOTH-ERR=4` (REST string range predicates rejected on both backends),
no `MISMATCH`, `DUP` or `TARGET-ERR`. Details in [results/README.md](results/README.md).

Helm chart on k3s: fault battery, readiness, cold start and rolling upgrade clean; two open findings
carried upstream (#3222 for the single-node PD, the store memory limit in `docs/store-memory-preset.md`).

## License

Apache License 2.0, the same license as HugeGraph, so patches and tests can move upstream as they are.
