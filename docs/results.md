# Results

All runs on the lab in [setup.md](setup.md). "1-store" = the cluster before nodes 2 and 3 existed.
Reports are in `results/`; `*.json` carry the full id sets and can be re-compared with
`suite/hg_suite.py compare a.json b.json --ids`.

## 1. Legacy sort-key suite (bash, expected counts) — the #3090 / PR #3184 axis

`cluster/legacy/hg_sortkeys_regression.sh`, 56 cases, hstore 1-store, expected counts hand-written.

| Server | hstore jar | PASS | FAIL | ERR | Report |
|---|---|---|---|---|---|
| master `98477f0` | master | 12 | 1 | **41** | `results/edge_master.txt` |
| master `98477f0` | PR #3184 | 48 | 2 | 4 | `results/edge_fix.txt` |

- The 41 errors on master are every case that pushes a condition into the store — prefix
  (`asset=ETC`), range, `within`, paging and their Gremlin equivalents — failing with
  `Can't construct Cardinality from code 0` / `Unsupported data type UNKNOWN` (findings F5).
- Of the 2 FAILs with the fix, one was a wrong expectation in the bash suite (multi-label case:
  8 edges, not 6) and one is F1 (`PAGED asset=ETC, page size 500: got=1214`). The bash suite could
  only *count* — it saw 1214 and could not tell duplicates from extra rows. That is the case for
  the oracle suite.
- The 4 ERR are the REST string predicates (F2).

## 1b. Oracle suite on master `98477f0` — before any patch (hstore 3-store vs rocksdb)

Server distributions built from a clean master worktree (`cluster/make_master_dists.sh`); the PD/store
cluster is the same (the PRs do not touch it). `results/master_*.{json,txt}`, `results/compare_master.txt`.

| Section | Cases | OK | MISMATCH | TARGET-ERR | BOTH-ERR |
|---|---|---|---|---|---|
| S sort keys / pushdown | 56 | 15 | 0 | **37** | 4 |
| L range index / paging | 43 | 43 | 0 | 0 | 0 |
| J label semantics | 35 | 32 | 0 | 1 | **2** |
| K within × search × range | 40 | 24 | **16** | 0 | 0 |
| **total** | **174** | **114** | **16** | **38** | **6** |

- S: every pushed-down sort-key condition crashes hstore (#3090, findings F5).
- J: `hasLabel(without(…))` + range-index property throws on **both** backends — a server-level gap in
  master, closed by #2994 (findings F9).
- K: with a SEARCH condition present hstore returns only the first `within()` value's results — wrong
  answers without an error, closed by #3182 (findings F8).
- L: clean on master too — #3140 is already merged there.

Version axis (`results/compare_master-oracle_vs_combined-oracle.txt`): rocksdb master vs rocksdb combined
= `OK=168 ORACLE-ERR=2 BOTH-ERR=4` — the PRs change server results only in the two `without()` cases
(master throws, combined returns 0 / 100). hstore master vs hstore combined = `OK=114 MISMATCH=16
ORACLE-ERR=40 BOTH-ERR=4`: 40 errors removed, 16 wrong sets fixed.

## 2. Oracle suite (Python, expectation = rocksdb) — `combined` server, hstore 3-store

`combined` = master `98477f0` + PR #3184 (`1072872`) + PR #3182 (`acbee167`, merged upstream 2026-09-03)
+ PR #2994 (`5cb9a519`) — exact recipe in [setup.md](setup.md#source-and-build). Fresh cluster, `--load` on both.

| Section | Cases | OK | MISMATCH | DUP | TARGET-ERR | BOTH-ERR |
|---|---|---|---|---|---|---|
| S sort keys / pushdown | 56 | 52 | 0 | 0 | 0 | 4 |
| L range index / paging | 43 | 43 | 0 | 0 | 0 | 0 |
| J label semantics | 35 | 35 | 0 | 0 | 0 | 0 |
| K within x search x range | 40 | 40 | 0 | 0 | 0 | 0 |
| **total** | **174** | **170** | 0 | 0 | 0 | 4 |

`results/combined_oracle.{json,txt}`, `results/combined_hstore.{json,txt}`, `results/compare_combined.txt`.
Oracle load + run: 8 s; hstore load + run: 24 s; full cycle including the cluster wipe: 3 min.

Both backends carry F1 identically here (`PAGED asset=ETC, page size 500 → n=1214 uniq=1212` on
**both**, hence `OK` — the oracle cannot see a server bug; the `uniq < n` on the oracle side is what
gave it away).

## 3. F1 patch — before/after

Patched `hugegraph-core` jar deployed to **both** distributions, data untouched, suite without `--load`:

| Comparison | Result |
|---|---|
| patched rocksdb vs **pre-fix** rocksdb report | `OK=169 MISMATCH=1 BOTH-ERR=4` — the one mismatch is `PAGED asset=ETC, page size 500`: 1214 → 1212, `only-oracle=0 only-target=0` (same unique set) |
| patched hstore vs pre-fix rocksdb report | identical to the row above |
| patched rocksdb vs patched hstore | `OK=170 MISMATCH=0 BOTH-ERR=4` |
| `page_probe.py`, both backends, sizes 100…1000, prefix / range / no-condition | `dups=0` everywhere (`results/probe_patched_rocksdb.txt`) |
| vertex label-index paging, sizes 500 / 1000, both backends | `n=3000 uniq=3000` |
| HugeGraph core test `EdgeCoreTest#testQueryOutEdgesOfVertexInPagingAtBatchBoundary` on rocksdb | unpatched: `limit 500 expected:<1200> but was:<1202>`; patched: pass |

`results/compare_pre-fix_oracle_vs_patched_hstore.txt` is the first row.

## Reading a report

Text report, one line per case:

```
CASE  S REST asset=ETC & epoch>=150      n=1207 uniq=1207 h=c20e4eafd343  [OK]
ERR   S REST asset>=ETC                  Invalid value 'ETC', expect a number  [BOTH-ERR]
```

`n` = elements returned, `uniq` = distinct ids, `h` = md5 of the sorted ids (12 hex chars). The
bracketed class appears when `--expect` was given. Scalar cases (determinism, tx deltas) print
`n=uniq=value h=v<value>`.

JSON report: `{"meta": {backend, version, host, port, sections, load, started, seconds, cases},
"cases": [{section, name, n, uniq, h, ids | err}]}`. `ids` is kept for sets up to 20 000 elements
(`--no-ids` drops it).
