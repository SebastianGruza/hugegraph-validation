# PR #2994 at head `2d53a55` — positive-label pushdown and unbound SEARCH, 2026-09-09

`2d53a55` (*fix(server): preserve positive label lookup and unbound search*) answers two of the three review notes of
2026-09-08: positive EQ/IN label containers are pushed into `HugeGraphStep` again when every candidate label has a
label index (`canPushPositiveLabel()`), and `LocalSearchHasContainer` is installed for unbound child traversals too
(the `tryGetGraph` gate is gone). The third note (per-element `Condition` allocation in `LocalContainsStep`) is
tracked separately in apache/hugegraph#3196.

**Erratum first.** The [`e32a75f` report](../e32a75f/README.md) of 2026-09-08 has an invalid rocksdb column: the
rocksdb server of that run was still on the `fefe3ca` core jar (`d71cc405`) because the dist-assembly script had been
piped into `head` and died before copying the rocksdb dist. Its hstore column and the master baseline are valid. The
"`HasStep(lambda)` on rocksdb with the same build" observation posted upstream on 2026-09-08 was this skew, not a
property of the PR; with both dists on the same jar the plan text is identical on both backends (below).
`cluster/run_side.sh` now refuses to run when the two dists of a tag carry different `hugegraph-core` jars.

Sides: `master` = `origin/master` `36811483` (server code unchanged since), `pr2994` = head `2d53a55`,
`pr2994p3184` = `2d53a55` + PR #3184 `0ecc10a3`. Core jar `f15f8aeb` verified identical in all four PR dists.

## 1. Backend axis — hstore vs rocksdb, same build

| Side | Suite (174) | hg_j8 (J8 138 / P 27 / G 42) |
|---|---|---|
| `pr2994` | OK=132, TARGET-ERR=38 (#3090), BOTH-ERR=4 | OK=190, J8 133/133, **P 27/27**, G positive controls on a cold hstore server as before |
| `pr2994p3184` | **OK=170, BOTH-ERR=4** | OK=190, J8 133/133, P 27/27 |

18 new J8 shapes (section J8j): a positive label followed by an unsafe label inside `where(out()...)`, with `within`,
conflicting and missing labels, `E().where(inV()...)`, and explicit-term `Text.contains('(gold)')` /
`'(gold|silver)'` inside a child traversal. All identical on hstore and rocksdb.

## 2. Version axis — rocksdb master vs rocksdb `2d53a55`

Suite unchanged (168 OK, the two `without()` cases master throws on). J8j against master:

| Query | master | `2d53a55` |
|---|---|---|
| `g.V().hasLabel('firm').where(__.out('deal').hasLabel(neq('person')))` and 11 other positive-label shapes | same sets | same sets |
| `g.V().hasLabel('nope').where(__.out().hasLabel(neq('person')))` | throws (undefined label) | 0 |
| `g.V().hasLabel('firm').where(__.out('deal').has('fname',Text.contains('(gold)')).hasLabel(neq('person')))` | **0** — `(gold)` matched as a literal substring, parentheses included | 1 — analysed term |
| same with `Text.contains('(gold|silver)')` | **0** | 1 |
| `g.V().has('fname',Text.contains('(gold)'))` (control, source step) | 80 | 80 |

So review note 2 of 2026-09-08 is measurable and fixed: in a child traversal master matches `Text.contains('(term)')`
as a substring and returns nothing, this head runs the SEARCH analyzer as `docs/negative-label-queries.md` promises.

## 3. Review note 1 at 1 000 000 vertices — positive label + unsafe child

`suite/scale_fallback.py`, section "positive", `count()` shapes, best of 2:

| Query | master hstore | master rocksdb | `2d53a55` hstore | `2d53a55` rocksdb |
|---|---|---|---|---|
| `g.V().hasLabel('firm').where(__.out('deal').hasLabel(neq('person'))).count()` | 16 ms | 7 ms | 14 ms | 6 ms |
| `g.V().hasLabel('robot').where(__.out().hasLabel(neq('person'))).count()` | 23 ms | 6 ms | 17 ms | 6 ms |
| `g.V().hasLabel(within('firm','robot')).where(__.out().hasLabel(neq('person'))).count()` | 31 ms | 8 ms | 30 ms | 11 ms |
| `g.V().hasLabel('firm').has('type',gte(2)).where(__.out('deal').hasLabel(neq('person'))).count()` | 17 ms | 7 ms | 16 ms | 4 ms |
| `g.V().hasLabel('firm').count()` (control) | 19 ms | 6 ms | 13 ms | 3 ms |

Plan at this head, both backends: `HugeGraphStep(Vertex,[~label.eq(firm)]), TraversalFilterStep([HugeVertexStep(OUT,[deal],vertex), HasStep([~label.neq(person)])])`
— the label-index lookup is back and the negative label is applied locally inside the child. With `type>=2` the
property predicate stays local next to the pushed label (`HasStep([type.gte(2)])` after the source step), as the
commit message says. The unchanged rows from the earlier reports (`has(age).out().hasLabel(neq)` 54 ms on hstore vs
2.2 s on master, the documented fallback failing at capacity after ~0.9 s on both backends) are in
`fallback_{pr2994,master}_{hstore,rocksdb}.json`.

## Reproduce

Same as the earlier reports; `suite/hg_j8.py` at this tag adds J8j and four plan shapes, `suite/scale_fallback.py`
the five "positive" rows. Check `hugegraph-core` md5 in both dists of a tag before trusting a backend-axis difference.
