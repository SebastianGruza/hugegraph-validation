# results/

Raw reports behind [docs/results.md](../docs/results.md) and [docs/findings.md](../docs/findings.md).
All runs on the lab described in [docs/setup.md](../docs/setup.md). Unless stated otherwise the server
code is `combined` = apache/hugegraph master `98477f0f56` + PR #3184 `1072872668` + PR #3182 `acbee16753`
(merged upstream 2026-09-03) + PR #2994 `5cb9a51956` — exact recipe in
[docs/setup.md](../docs/setup.md#source-and-build) — built on Temurin 17 and run on Temurin 11.

## Files

| File | Produced by | Server / backend | When | Content |
|---|---|---|---|---|
| `edge_master.txt` | `cluster/legacy/hg_sortkeys_regression.sh` (`LOAD=1`) | master `98477f0`, **master** `hugegraph-hstore` jar, hstore **1-store** | 2026-09-02 18:46 | legacy bash suite with hand-written expected counts: 54 judged lines (52 queries + 2 load-time Gremlin inserts) — **PASS 12 / FAIL 1 / ERR 41** |
| `edge_fix.txt` | same | master `98477f0`, `hugegraph-hstore` jar from **PR #3184** (`847fab9`), hstore 1-store | 2026-09-02 18:48 | **PASS 48 / FAIL 2 / ERR 4** |
| `combined_oracle.txt`, `.json` | `suite/hg_suite.py run --port 8081 --load --out …` | combined, **rocksdb** (the oracle) | 2026-09-03 09:49 | fresh graph, 174 cases in 8 s; the `.json` holds the full id set of every case |
| `combined_hstore.txt`, `.json` | `suite/hg_suite.py run --port 8080 --load --expect combined_oracle.json --out …` | combined, **hstore, PD + 3 stores** | 2026-09-03 09:49 | fresh graph, 174 cases in 24 s, every case classified inline against the oracle |
| `compare_combined.txt` | `suite/hg_suite.py compare combined_oracle.json combined_hstore.json --ids` | — | 2026-09-03 09:50 | class summary per section |
| `patched_oracle.json` | `suite/hg_suite.py run --port 8081 --out …` (no `--load`) | combined **+ `patches/0001` (F1 fix)** in `hugegraph-core`, rocksdb | 2026-09-03 09:57 | same data as the combined cycle, patched server |
| `patched_hstore.json` | `suite/hg_suite.py run --port 8080 --out …` (no `--load`) | combined + F1 fix, hstore 3-store | 2026-09-03 09:57 | same |
| `compare_pre-fix_oracle_vs_patched_hstore.txt` | `compare combined_oracle.json patched_hstore.json --ids` | — | 2026-09-03 09:58 | isolates what the patch changed: exactly one case |
| `probe_patched_rocksdb.txt` | `suite/page_probe.py --port 8081` | combined + F1 fix, rocksdb | 2026-09-03 09:58 | paging probe after the fix: 3 query shapes × 7 page sizes, all `dups=0` |
| `master_oracle.txt`, `.json` | `cluster/run_cycle.sh master` → `hg_suite.py run --port 8081 --load` | **master `98477f0`, no patches** (server built from a clean worktree, `cluster/make_master_dists.sh`), rocksdb | 2026-09-03 17:19 | the "before" oracle: fresh graph, 174 cases in 8.5 s |
| `master_hstore.txt`, `.json` | same cycle → `run --port 8080 --load --expect master_oracle.json` | master `98477f0`, hstore PD + 3 stores | 2026-09-03 17:19 | the "before" run under test: 174 cases in 25.6 s |
| `compare_master.txt` | `compare master_oracle.json master_hstore.json --ids` | — | 2026-09-03 17:20 | backend divergences **before any patch** |
| `compare_master-oracle_vs_combined-oracle.txt` | `compare master_oracle.json combined_oracle.json --ids` | rocksdb vs rocksdb | 2026-09-03 17:22 | **version axis**: what the three PRs change in the server itself, backend held constant |
| `compare_master-hstore_vs_combined-hstore.txt` | `compare master_hstore.json combined_hstore.json` | hstore vs hstore | 2026-09-03 17:22 | what the PRs change for hstore users |
| `pd_watch_3152_master_drop.log`, `pd_watch_3152_pr3157_drop.log` | `cluster/pd_watch_exp2.sh` | master `98477f0` vs PR #3157 `f99b6bd`, two servers in PD-meta mode on the 3-store cluster | 2026-09-03 20:24 / 20:29 | PD KV watch recovery across two 30 s PD restarts (finding F11): master stale after every outage, PR recovers in 1 s |
| `pd_watch_3152_master_add.log`, `pd_watch_3152_pr3157_add.log` | `cluster/pd_watch_exp.sh` (first variant) | same | 2026-09-03 20:18 / 20:21 | graph-add observable (compensated by lazy loading — kept for the `Accept graph add signal` counts: master 1/0/0, PR 1/1/1) |

Text reports: one `CASE <section> <name> n=<returned> uniq=<distinct ids> h=<md5 of sorted ids>` line per
case (`ERR … <message>` for a failing query); with `--expect` the class is appended in brackets.
JSON reports: `{"meta": {backend, version, host, port, sections, load, started, seconds, cases},
"cases": [{section, name, n, uniq, h, ids} | {section, name, err}]}`.

## Aggregated results

### 1. Legacy sort-key suite — the #3090 / PR #3184 axis (hstore 1-store, expected counts)

| hstore jar | PASS | FAIL | ERR | Notes |
|---|---|---|---|---|
| master | 12 | 1 | **41** | every pushed-down condition (prefix, range, `within`, paging, their Gremlin forms) fails in store-side row decoding: `Can't construct Cardinality from code 0` / `Unsupported data type UNKNOWN` |
| PR #3184 | 48 | 2 | 4 | the 4 ERR are REST string predicates (F2); of the 2 FAIL one was a wrong expectation in the bash suite (multi-label case returns 8, not 6) and one is F1 (`PAGED asset=ETC, page size 500: got=1214` — a count cannot tell duplicates from extra rows) |

### 1b. Oracle suite on **master `98477f0` before any patch** — hstore 3-store vs rocksdb, fresh `--load` on both

Same suite, same cluster, server distributions built from a clean `98477f0` worktree (PD and the store
nodes are unchanged by the PRs, so they stay). This is the baseline every PR is measured against.

| Section | Cases | OK | MISMATCH | TARGET-ERR | BOTH-ERR | What is wrong on master |
|---|---|---|---|---|---|---|
| S — sort keys / pushdown | 56 | 15 | 0 | **37** | 4 | every query that pushes a sort-key prefix/range/`within`/paging condition into the store fails in store-side decoding (`Can't construct Cardinality from code 0`, `Unsupported data type UNKNOWN`) — #3090; only no-condition, full-equality (`IdPrefixQuery`) and in-memory-filtered shapes survive |
| L — range index / paging | 43 | 43 | 0 | 0 | 0 | clean — cross-partition ordering/paging of #3140 is already in master |
| J — label semantics | 35 | 32 | 0 | 1 | **2** | `hasLabel(without(...))` combined with a range-index property **throws on both backends** (`Can't do index query with [LABEL != 5, LABEL != 6] and [12 >= 30]`, `Don't accept query based on properties [age] that are not indexed in any label…`) — server bug, not backend; the 1 `TARGET-ERR` is the #3090 shape via Gremlin (`outE('flow').has('asset','ETC')`) |
| K — within × search × range | 40 | 24 | **16** | 0 | 0 | with a **SEARCH-index condition present, hstore honours only the first value of `within(...)`**: `within(1,2,3)` returns 20 of 50/60, `within(1,2,3,4)` 20 of 80, `within(1,3)` 20 of 40 — silently wrong results (no error); without the search condition (`range+within no search`) the same `within` is correct; identical on the cache hit; the K5 transaction test additionally shows hstore losing one element on commit (`after − before = −1`, oracle 0) |
| **Total** | **174** | **114** | **16** | **38** | **6** | |

### 1c. Version axis — same backend, master vs `combined`

| Comparison | Result | Meaning |
|---|---|---|
| rocksdb master vs rocksdb combined | `OK=168 ORACLE-ERR=2 BOTH-ERR=4` | the three PRs change server semantics in exactly two cases — the two `without()` + range-index queries that **throw on master and return results on combined** (0 and 100 vertices, matching hstore); the other 168 cases are identical sets → no semantic regression from #2994 / #3182 on this suite |
| hstore master vs hstore combined | `OK=114 MISMATCH=16 ORACLE-ERR=40 BOTH-ERR=4` | for hstore users the PRs remove 40 errors (37 × #3090 shapes via #3184, 3 × label/`without` via #2994) and fix the 16 wrong `within()`-with-search results (#3182) |

### 2. Oracle suite — `combined` server, hstore 3-store vs rocksdb, fresh `--load` on both

| Section | Territory | Cases | OK | MISMATCH | DUP | TARGET-ERR | BOTH-ERR |
|---|---|---|---|---|---|---|---|
| S — sort keys / pushdown | #3090, PR #3184 | 56 | 52 | 0 | 0 | 0 | 4 |
| L — range index / paging | #3140 | 43 | 43 | 0 | 0 | 0 | 0 |
| J — label semantics | PR #2994 | 35 | 35 | 0 | 0 | 0 | 0 |
| K — within × search × range | PR #3182 | 40 | 40 | 0 | 0 | 0 | 0 |
| **Total** | | **174** | **170** | **0** | **0** | **0** | **4** |

- 170 cases compared as id sets: **67 145 element ids returned by each backend, sets identical case by case**
  (167 set cases + 3 scalar cases: 10× determinism of the #3180 combo, and the two uncommitted-transaction deltas).
- The 4 `BOTH-ERR` are the REST `properties` string range predicates — `asset>=ETC`, `ETC<=asset<ETC!`,
  `asset>=ÉTC`, `asset>=～` — rejected by the REST parser on both backends (`Invalid value 'ETC', expect a number`).
  The same ranges via Gremlin are among the 170 `OK`.
- Both backends carry the F1 duplicate identically here (`S PAGED asset=ETC, page size 500`: `n=1214 uniq=1212`
  on **both**, hence `OK`) — the oracle cannot see a bug shared by the server; the `uniq < n` on the oracle side is what exposed it.
- Timing: oracle load + run 8 s, hstore load + run 24 s, whole cycle including the cluster wipe ~3 min (`cluster/run_cycle.sh`).

### 3. Effect of the F1 patch (`patches/0001`), deployed to both distributions

| Comparison | Result |
|---|---|
| patched hstore vs **pre-fix** oracle report | `OK=169 MISMATCH=1 BOTH-ERR=4` — the one mismatch is `S PAGED asset=ETC, page size 500`: 1214 → 1212, `only-oracle=0 only-target=0` (identical unique set, only the two duplicates are gone) |
| patched rocksdb vs pre-fix oracle report | identical to the row above |
| patched rocksdb vs patched hstore | `OK=170 MISMATCH=0 BOTH-ERR=4` |
| elements returned over all set cases | 67 145 → **67 143** on both backends (−2 = the duplicated boundary records) |
| `page_probe.py`, sizes 100 / 250 / 333 / 400 / 500 / 600 / 1000 × no-condition / prefix / range | 21 rows, `dups=0` in all (before the fix: sizes 500 and 1000 duplicated one record per page boundary) |
| HugeGraph core test `EdgeCoreTest#testQueryOutEdgesOfVertexInPagingAtBatchBoundary`, rocksdb profile | before: `limit 500 expected:<1200> but was:<1202>`; after: pass (log not in this directory — see `cluster/validate_patch.sh`) |

## Reproducing

```bash
# oracle first, then the backend under test, then compare (two servers up — see docs/setup.md)
python3 suite/hg_suite.py run --port 8081 --load --out oracle.json
python3 suite/hg_suite.py run --port 8080 --load --out hstore.json --expect oracle.json
python3 suite/hg_suite.py compare oracle.json hstore.json --ids

# whole cycle on the 3-node lab, including the cluster wipe
cluster/run_cycle.sh combined

# paging probe on one server
python3 suite/page_probe.py --port 8081
```
