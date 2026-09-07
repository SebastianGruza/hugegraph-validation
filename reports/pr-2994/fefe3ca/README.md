# PR #2994 at head `fefe3ca` — re-measurement of 2026-09-07

Follow-up to [the 2026-09-06 report on `ac641c6`](../README.md). Same lab, same method, same suite plus 27 new
J8 shapes for the review findings of 2026-09-07 (`hasKey()` / `hasValue()` and connective string ids next to a
negative label) and three new `explain()` plan shapes.

## Server versions

| Side | Tree |
|---|---|
| `master` | `origin/master` `36811483` (unchanged for server code; `bed2e457` on top is docker-only, #3187) |
| `pr2994` | PR #2994 head `fefe3ca0` *fix(server): preserve system predicates in local label filtering* — fast-forward from `ac641c6`, contains `36811483` |
| `pr2994p3184` | `pr2994` + PR #3184 head `0ecc10a3` (merge `6e1e74cd`), hstore backend axis only |

`hugegraph-core` jar `d71cc405` is identical between `pr2994` and `pr2994p3184`.

## 1. Backend axis — hstore vs rocksdb

| Side | Suite (174) | hg_j8 (J8 120 / P 23 / G 42) |
|---|---|---|
| `master` | OK=130, TARGET-ERR=38, BOTH-ERR=6 | OK=160, TARGET-ERR=1, BOTH-ERR=24 |
| `pr2994` | OK=132, TARGET-ERR=38 (#3090), BOTH-ERR=4 | OK=168, MISMATCH=6, TARGET-ERR=6, BOTH-ERR=5 |
| `pr2994p3184` | **OK=170, BOTH-ERR=4, no MISMATCH / DUP / TARGET-ERR** | OK=168, MISMATCH=6, TARGET-ERR=6, BOTH-ERR=5 |

J8 on the PR: **115/115 OK**, every `hasKey`/`hasValue`/connective-id shape returns the same id set on hstore and
rocksdb. The 5 `BOTH-ERR` are server-side rejections identical on both backends (`g.V().hasKey('age')` and
`g.V().hasValue(20)` alone: `Not support query: ... PROPERTIES containsk/containsv` — the backends do not run
CONTAINS queries; `hasKey('age','score')`: `CONTAINS query with relation 'within' is not supported`;
`hasKey('nope')`: `Undefined property key`; and the `robot score>=40` positive control from the first report).
The 6 `TARGET-ERR` + 6 `MISMATCH` are the G paging positive controls hitting `Not allowed to access thread group via
Gremlin` on a cold hstore server — the pre-existing store-client lazy-initialisation issue recorded in §4 of the first
report, unchanged. Files: `compare_*.txt`, `j8_compare_*.txt`.

## 2. Version axis — rocksdb master vs rocksdb pr2994

Suite: `OK=168, ORACLE-ERR=2, BOTH-ERR=4`, unchanged from `ac641c6` (the two `without()` cases master throws on).
J8: `OK=76, MISMATCH=31, ORACLE-ERR=8, TARGET-ERR=1, BOTH-ERR=4`. The 18 `score`-index mismatches of the first
report are unchanged (master 0 / PR 60). The new shapes:

| Query | master | pr2994 |
|---|---|---|
| `g.V().hasKey('age').hasLabel(neq('person'))` | **0** | 300 |
| `g.V().hasLabel(neq('person')).hasKey('age')` | **0** | 300 |
| `g.V().hasKey('score').hasLabel(neq('robot'))` | **0** | 3000 |
| `g.V().hasKey('fname').hasLabel(neq('person'))` | **0** | 200 |
| `g.V().hasKey('age').has('age',gte(60)).hasLabel(neq('person'))` | **0** | 100 |
| `g.V().hasValue(20).hasLabel(neq('person'))`, also `hasKey('age').hasValue(20)` | **0** | 5 |
| `g.V().hasLabel('firm').out('deal').hasKey('type').hasLabel(neq('person'))` | **0** | 100 |
| `g.V('a').outE().hasKey('amount').hasLabel(neq('flow'))` | **0** | 120 |
| `g.V('a').outE().hasValue('ETC').hasLabel(neq('flow'))` | **0** | 60 |
| `g.E().hasKey('amt').hasLabel(neq('flow'))` | **0** | 100 |
| `g.V().hasKey('age').limit(100000).hasLabel(neq('person'))`, `hasValue(20).limit(..)`, `hasKey('age').where(not(hasLabel))` | throws `Not support query: ... containsk` | 300 / 5 / 300 |
| `g.V().hasId(within('p00010','r0001').and(neq('r0001'))).limit(10).hasLabel(neq('robot'))` | throws `Not supported querying by id and conditions` | 1 |
| `g.V().hasId(eq('p00010').and(neq('r0001'))).limit(10).hasLabel(neq('robot'))` | throws | 1 |
| `g.V().hasId(within('p00010','r0001').or(eq('f0001'))).limit(10).hasLabel(neq('person'))` | throws | 2 |
| `g.V().hasKey('nope').hasLabel(neq('person'))` | 0 | throws `Undefined property key: 'nope'` |

So on master a `hasKey()`/`hasValue()` next to a negative label is not merely unsupported: it returns an **empty set
without an error** in every one of ten shapes (the bare `hasKey()` throws, the one with a label predicate answers 0).
At `fefe3ca` every shape returns the complete set, identically on hstore. The one direction change is `hasKey` of an
undefined key: master 0, PR an error — the error is the more useful answer.

Files: `compare_version-axis_rocksdb_master_vs_pr2994.txt`, `j8_compare_version-axis_rocksdb_master_vs_pr2994.txt`.

## 3. Plan and cost axis — 1 000 000 `big` vertices on rocksdb

| Query | master | pr2994 `ac641c6` (09-06) | pr2994 `fefe3ca` (09-07) |
|---|---|---|---|
| `g.V('big00000010').limit(10).hasLabel(neq('mark'))` and the `hasId` / two-id variants | 3 ms | 7–10 ms | **2 ms**, plan `HugeGraphStep(vertex,[big00000010])` |
| `g.V().has('age',gte(60)).out().hasLabel(neq('person'))` | 17 ms | `Too many records` | **12 ms**, plan `HugeGraphStep(Vertex,[age.gte(60)]), HugeVertexStep(OUT,vertex), HasStep([~label.neq(person)])` |
| `g.V().has('age',gte(60)).out().where(__.not(__.hasLabel('person')))` | 18 ms | `Too many records` | **17 ms** |
| `g.V().has('age',gte(60)).limit(100000).hasLabel(neq('person'))` | 8 ms, incomplete | `Too many records` | `Too many records` (deliberate: complete answer or none) |
| `g.V().has('score',gte(40)).limit(100000).hasLabel(neq('person'))` | 11 ms, incomplete | `Too many records` | `Too many records` |
| `g.V().has('fname',Text.contains('gold')).limit(100000).hasLabel(neq('person'))` | 7 ms | `Too many records` | `Too many records`, plan `HasStep(lambda)` |
| `g.V().has('age',gte(60)).hasLabel(neq('person'))`, no barrier | `Too many records` | `Too many records` | `Too many records` |

Finding 2 of the 2026-09-06 review (the gate firing for labels that only apply after an element-changing step) is
closed at `fefe3ca`: the index lookup is back, faster than master, and the label is applied locally after `out()`.

Two new shapes measured at 1 M for the code added in `fefe3ca` (`LocalIdHasContainer`, `LocalContainsStep`), not
regressions since master throws on both, but both are full scans where a point lookup would be complete:

| Query | master | pr2994 `fefe3ca` |
|---|---|---|
| `g.V().hasId(within('big00000010','mark0').and(neq('mark0'))).limit(10).hasLabel(neq('mark'))` | `Not supported querying by id and conditions` | `Too many records`, plan `HugeGraphStep(vertex,[]), HasStep([~id.and(within(..), neq(..))]), ...` |
| `g.V().hasId(eq('big00000010').and(neq('mark0'))).limit(10).hasLabel(neq('mark'))` | same | `Too many records` |
| `g.V().hasId(within('big00000010','mark0')).limit(10).hasLabel(neq('mark'))` (no connective) | 3 ms | **39 ms**, point lookup |
| `g.V().hasKey('age').hasLabel(neq('person'))` | 0, silently | `Too many records`, plan `HugeGraphStep(vertex,[]), LocalContainsStep([~key.eq(age)]), HasStep(..)` |

`VertexCoreTest#testLocalConnectiveStringIds` pins the result set of the connective shapes on a small graph; a plan
assertion would pin that the id set still reaches `HugeGraphStep` (the `within` values are a complete candidate
list even when a `neq` leaf is attached). `hasKey()` has no index to push, so a full scan is the only complete plan
there; that one is a limitation to record, not a fix to request.

## Reproduce

Same as [the first report](../README.md#reproduce), with `suite/hg_j8.py` at this tag (J8i and the three extra plan
shapes) and `~/hg-pr2994` at `fefe3ca`. Servers and backends are in the `meta` block of every JSON report.
