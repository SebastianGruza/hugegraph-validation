#!/usr/bin/env python3
"""scale_fallback.py — the documented local-filter fallback of PR #2994 (docs/negative-label-queries.md) measured
at scale: an unindexed or index-abandoned property next to an unsafe label predicate on a graph with 1 M extra
vertices. Records, per query: wall time, result / count, error text, and the explain() plan. Also times a full
~page sweep with a downstream negative label until the cursor is exhausted.

usage: python3 scale_fallback.py --port 8080 [--load --n 1000000] [--out fallback_hstore.json]
       (--load adds the 'big'/'mark' vertices of scale_probe.py; run it once per backend on top of the suite data)"""
import argparse, json, sys, time
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from hg_suite import HG
from hg_j8 import final_plan, page_all
from scale_probe import load, timed

QUERIES = [
    # unindexed property (cnt: no index on any label) + negative label — the exact shape of docs/negative-label-queries.md
    ("unindexed: V cnt=5 (control, alone)", "g.V().has('cnt',5).id()"),
    ("unindexed: V cnt=5 neq(person) ids", "g.V().has('cnt',5).hasLabel(neq('person')).id()"),
    ("unindexed: V cnt=5 limit(100000) neq(person) ids", "g.V().has('cnt',5).limit(100000).hasLabel(neq('person')).id()"),
    ("unindexed: V cnt=5 neq(person) count", "g.V().has('cnt',5).hasLabel(neq('person')).count()"),
    ("unindexed: V cnt=5 neq(person) limit(5)", "g.V().has('cnt',5).hasLabel(neq('person')).limit(5).id()"),
    ("unindexed: V cnt=5 eq(person) limit(5) (positive label, control)", "g.V().has('cnt',5).hasLabel('person').limit(5).id()"),
    # index abandoned by the gate (age indexed on person + robot, not on big/mark)
    ("abandoned: V age>=60 limit(100000) neq(person) ids", "g.V().has('age',gte(60)).limit(100000).hasLabel(neq('person')).id()"),
    ("abandoned: V age>=60 limit(100000) neq(person) count", "g.V().has('age',gte(60)).limit(100000).hasLabel(neq('person')).count()"),
    ("abandoned: V age>=60 neq(person) limit(5)", "g.V().has('age',gte(60)).hasLabel(neq('person')).limit(5).id()"),
    ("abandoned: V age>=60 neq(person) count", "g.V().has('age',gte(60)).hasLabel(neq('person')).count()"),
    ("abandoned: V score>=40 limit(100000) neq(person) count", "g.V().has('score',gte(40)).limit(100000).hasLabel(neq('person')).count()"),
    ("abandoned: V fname contains(gold) limit(100000) neq(person) count", "g.V().has('fname',Text.contains('gold')).limit(100000).hasLabel(neq('person')).count()"),
    # what the docs recommend instead
    ("recommended: V robot age>=60 count (positive label)", "g.V().hasLabel('robot').has('age',gte(60)).count()"),
    ("recommended: V within(robot,firm) age>=60 count", "g.V().hasLabel(within('robot','firm')).has('age',gte(60)).count()"),
    ("recommended: V(r0001,r0002) neq(person) (explicit ids)", "g.V('r0001','r0002').hasLabel(neq('person')).id()"),
    # still an index lookup after fefe3ca
    ("kept: V age>=60 out() neq(person) count", "g.V().has('age',gte(60)).out().hasLabel(neq('person')).count()"),
    ("kept: V age>=60 count (control)", "g.V().has('age',gte(60)).count()"),
    # positive label + unsafe label in a child traversal (review 2026-09-08 note 1, head 2d53a55)
    ("positive: V firm where(out(deal).neq(person)) count", "g.V().hasLabel('firm').where(__.out('deal').hasLabel(neq('person'))).count()"),
    ("positive: V robot where(out().neq(person)) count (robots have no edges)", "g.V().hasLabel('robot').where(__.out().hasLabel(neq('person'))).count()"),
    ("positive: V within(firm,robot) where(out().neq(person)) count", "g.V().hasLabel(within('firm','robot')).where(__.out().hasLabel(neq('person'))).count()"),
    ("positive: V firm type>=2 where(out(deal).neq(person)) count", "g.V().hasLabel('firm').has('type',gte(2)).where(__.out('deal').hasLabel(neq('person'))).count()"),
    ("positive: V firm count (control)", "g.V().hasLabel('firm').count()"),
]

PAGES = [
    ("page: age>=60 neq(person), size 500", "g.V().has('~page',PAGE).has('age',gte(60)).hasLabel(neq('person'))", 500),
    ("page: age>=60 neq(person), size 5000", "g.V().has('~page',PAGE).has('age',gte(60)).hasLabel(neq('person'))", 5000),
    ("page: cnt=5 neq(person), size 5000", "g.V().has('~page',PAGE).has('cnt',5).hasLabel(neq('person'))", 5000),
    ("page: robot age>=60, size 500 (positive label, control)", "g.V().has('~page',PAGE).hasLabel('robot').has('age',gte(60))", 500),
]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, required=True); ap.add_argument("--n", type=int, default=1000000)
    ap.add_argument("--load", action="store_true"); ap.add_argument("--out"); ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--max-pages", type=int, default=5000)
    a = ap.parse_args(); hg = HG("localhost", a.port, "DEFAULT", "hugegraph"); info = hg.info(); print("server:", info)
    if a.load: load(hg, a.n)
    print("total V:", hg.ids_grem("g.V().count()")[0])
    rows = []
    for name, g in QUERIES:
        ms, n, err = timed(hg, g, a.repeat)
        plan, perr = final_plan(hg, g.rsplit(".", 1)[0])
        row = {"name": name, "query": g, "ms": round(ms), "n": n, "err": err, "plan": (plan or [perr])[0]}
        rows.append(row); print(f"{name:66} {row['ms']:>7} ms  n={n}  {('ERR ' + err[:90]) if err else ''}\n{'':66}   {row['plan'][:170]}", flush=True)
    for name, body, size in PAGES:
        t0 = time.time(); ids, pages, err = page_all(hg, body, size, a.max_pages); ms = round((time.time() - t0) * 1000)
        row = {"name": name, "query": body, "size": size, "ms": ms, "n": None if ids is None else len(ids), "pages": pages, "err": err}
        rows.append(row); print(f"{name:66} {ms:>7} ms  n={row['n']} pages={pages}  {('ERR ' + err[:90]) if err else ''}", flush=True)
    if a.out: json.dump({"meta": dict(info, port=a.port), "rows": rows}, open(a.out, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__": main()
