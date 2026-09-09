# PR #2994 at head `e32a75f` — the documented fallback measured at scale, 2026-09-08

> **Erratum (2026-09-09).** The rocksdb server of this run was still on the `fefe3ca` core jar (`d71cc405`): the
> dist-assembly script had been piped into `head` and died before copying the rocksdb dist. Every *rocksdb* column
> below therefore measures `fefe3ca`, not `e32a75f`; the hstore columns, the master baseline and the `fefe3ca → e32a75f`
> hstore diff are valid. The "`HasStep(lambda)` on rocksdb with the same build" observation in §1 was this skew, not a
> property of the PR. Corrected measurements on the next head: [`2d53a55`](../2d53a55/README.md).

Follow-up to the [`ac641c6`](../README.md) and [`fefe3ca`](../fefe3ca/README.md) reports. `e32a75f`
(*fix(server): preserve search predicates and ram label queries*) answers the three review notes of 2026-09-07:
`docs/negative-label-queries.md` documents the local-filter fallback, `LocalSearchHasContainer` replaces the lambda
`P`, `RamTable.matched()` accepts multi-label edge queries. This report re-runs the suites and then measures what the
new document describes but its tests (Memory, RocksDB) cannot show: the fallback on **HStore** with 1 000 000 vertices.

Sides: `master` = `origin/master` `36811483` (server code unchanged since; later commits are docker-only),
`pr2994` = head `e32a75f0`, `pr2994p3184` = `e32a75f0` + PR #3184 `0ecc10a3` for the hstore axis. Core jar `394044ee`
identical between the two PR variants.

## 1. Regression: nothing changed between `fefe3ca` and `e32a75f` in any result set

| Comparison | Result |
|---|---|
| suite, rocksdb `fefe3ca` → `e32a75f` | OK=170, BOTH-ERR=4, no MISMATCH |
| hg_j8, rocksdb `fefe3ca` → `e32a75f` | OK=180, BOTH-ERR=5, no MISMATCH (J8 115/115, P 23/23, G 42/42) |
| hg_j8, hstore `fefe3ca` → `e32a75f` | OK=173, BOTH-ERR=11 (the cold-server G paging controls), one plan-text difference below |
| backend axis `pr2994p3184`, suite | **OK=170, BOTH-ERR=4** |
| backend axis `pr2994p3184`, hg_j8 | OK=167, J8 115/115, G positive controls on a cold hstore server as before |

The one plan-text difference: `g.V().has('fname',Text.contains('gold')).limit(100000).hasLabel(neq('person'))`
now explains as `HasStep([fname.TEXT_CONTAINS(gold)])` on hstore (the `P` tree is preserved, review note 2), but the
same build on rocksdb still prints `HasStep(lambda)` — same result set on both. Recorded as an observation for the
author; it may only be `toString()` of a container whose matcher was already built.

## 2. The documented fallback at 1 000 000 vertices, `e32a75f` vs `master`, hstore vs rocksdb

`suite/scale_fallback.py`: 1 M `big` vertices (property `k`, no index) + 1 `mark` next to the suite data; `cnt` is a
defined property with no index on any label (persons carry it, robots do not), `age`/`score`/`fname` are indexed on
some labels only. Times are the best of 2 runs; `Query.DEFAULT_CAPACITY` = 800 000.

| Query | master hstore | master rocksdb | `e32a75f` hstore | `e32a75f` rocksdb |
|---|---|---|---|---|
| `g.V().has('cnt',5)` (control, alone) | `NoIndexException` 17 ms | 14 ms, same | `NoIndexException` 5 ms | 5 ms, same |
| `g.V().has('cnt',5).hasLabel(neq('person'))` | `Too many records` 811 ms | 816 ms, same | `Too many records` 965 ms | 1000 ms, same |
| `g.V().has('cnt',5).limit(100000).hasLabel(neq('person'))` | `NoIndexException` 12 ms | 11 ms, same | **`Too many records` 920 ms** | 1001 ms, same |
| `g.V().has('cnt',5).hasLabel(neq('person')).count()` | `Too many records` 772 ms | 805 ms | `Too many records` 845 ms | 990 ms |
| `g.V().has('cnt',5).hasLabel(neq('person')).limit(5)` (no match exists) | `Too many records` 736 ms | 804 ms | `Too many records` 906 ms | 982 ms |
| `g.V().has('age',gte(60)).limit(100000).hasLabel(neq('person'))` | 100 ids, 108 ms | 19 ms | **`Too many records` 865 ms** | 976 ms |
| `g.V().has('age',gte(60)).limit(100000).hasLabel(neq('person')).count()` | 89 ms | 18 ms | `Too many records` 839 ms | 989 ms |
| `g.V().has('age',gte(60)).hasLabel(neq('person')).limit(5)` (matches exist) | 5 ids, 94 ms | 9 ms | **5 ids, 63 ms** | 3 ms |
| `g.V().has('age',gte(60)).hasLabel(neq('person')).count()` (no barrier) | `Too many records` 854 ms | 896 ms | `Too many records` 866 ms | 958 ms |
| `g.V().has('score',gte(40)).limit(100000).hasLabel(neq('person')).count()` | 116 ms (incomplete on master, see the first report) | 20 ms | `Too many records` 921 ms | 1054 ms |
| `g.V().has('fname',Text.contains('gold')).limit(100000).hasLabel(neq('person')).count()` | 38 ms | 13 ms | `Too many records` 865 ms | 987 ms |
| `g.V().hasLabel('robot').has('age',gte(60)).count()` (what the doc recommends) | 39 ms | 10 ms | 13 ms | 3 ms |
| `g.V('r0001','r0002').hasLabel(neq('person'))` (explicit ids) | 8 ms | 8 ms | 2 ms | 2 ms |
| `g.V().has('age',gte(60)).out().hasLabel(neq('person')).count()` (kept index lookup since `fefe3ca`) | **2522 ms** | 44 ms | **49 ms** | 10 ms |

What this establishes for the document:

- **HStore enforces the capacity check on this path.** Every fallback shape fails with `Too many records(must <=
  800000)` after ~0.9 s on hstore exactly as on rocksdb, including the `count()` variants. The document's caveat that
  "some count paths disable capacity checks" did not materialise for any of these shapes on either backend.
- **`limit()` after the label predicate terminates early when matches exist** (63 ms on hstore for 5 robots), and
  scans to the capacity limit when none exist (`cnt=5` has no non-person match). The document's sentence "a final
  `limit()` bounds returned matches rather than all candidates examined" is exact.
- The two rows in bold in the `e32a75f` column are the contract change itself: `has('cnt',5).limit(..).hasLabel(neq(..))`
  went from a 12 ms `NoIndexException` to a 0.9 s capacity exception, and `has('age',gte(60)).limit(..).hasLabel(neq(..))`
  from a 108 ms complete answer (age is indexed on both labels that carry it) to a capacity exception. Both are the
  documented trade-off; the second one is the price paid on schemas where the abandoned index would have been complete.
- `has('age',gte(60)).out().hasLabel(neq('person'))` on **master hstore** takes 2.5 s against 49 ms on the PR: master
  pushes the label into `HugeVertexStep(OUT,Vertex,[~label.neq(person)])` and hstore evaluates it per adjacent-edge
  query; the PR's local `HasStep` after the vertex step is 50× faster on hstore and 4× on rocksdb.

## 3. The documented paging guidance at 1 000 000 candidates

`has('~page', cursor)` with a downstream negative label, cursor followed until exhaustion (`hg_j8.page_all`):

| Query, page size | master hstore | master rocksdb | `e32a75f` hstore | `e32a75f` rocksdb |
|---|---|---|---|---|
| `has('age',gte(60)).hasLabel(neq('person'))`, 500 | Gremlin `Read timed out` after 30 s, 0 pages | `Too many records` after 2.9 s | **100 ids, 2008 pages, 56.5 s** | 100 ids, 2008 pages, 38.0 s |
| same, 5000 | timed out, 30 s | `Too many records` 2.9 s | 100 ids, 201 pages, 6.2 s | 100 ids, 201 pages, 5.3 s |
| `has('cnt',5).hasLabel(neq('person'))`, 5000 (no match) | timed out, 30 s | `Too many records` 2.8 s | 0 ids, 201 pages, 6.6 s | 0 ids, 201 pages, 5.4 s |
| `hasLabel('robot').has('age',gte(60))`, 500 (positive label, control) | cold-server sandbox error | 100 ids, 1 page, 51 ms | cold-server sandbox error | 100 ids, 1 page, 4 ms |

So paging is the only way the fallback completes at this size on either backend, and it costs about 28 ms per page
on hstore (3 store nodes) and 19 ms on rocksdb regardless of page size, i.e. the sweep cost is dominated by the number
of round trips: page size 5000 is 9× cheaper than 500 for the same 100 results. On master the same paged query does
not complete at all on hstore (30 s Gremlin evaluation timeout) and hits capacity on rocksdb. The document's
instruction to continue past empty pages is what the 201/2008 empty pages are.

Not measured: `RamTable` (requires numeric vertex ids; the suite uses string ids).

## Reproduce

```bash
for T in pr2994 pr2994p3184; do cluster/run_side.sh $T; done
python3 suite/scale_fallback.py --port 8080 --load --out fallback_pr2994_hstore.json     # loads 1 M into hstore (27 s)
python3 suite/scale_fallback.py --port 8081 --load --out fallback_pr2994_rocksdb.json
# swap the server dists to master without wiping (copy rocksdb-data), then the same two commands without --load
```

Files: `compare_*.txt`, `j8_compare_*.txt`, `*_oracle.json`, `*_hstore.json`, `fallback_{pr2994,master}_{hstore,rocksdb}.json`.
