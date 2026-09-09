#!/usr/bin/env python3
"""hg_j8.py — PR #2994 review shapes, run read-only on top of the data loaded by hg_suite.py (sections S, L, J, K).

Sections:
  J8  label predicates in the shapes raised in the #2994 review (2026-08-31 .. 2026-09-06): negative label behind a
      barrier-like step (skip/limit/range/aggregate/coin/dedup/order), property indexed on one label only (score:
      person yes, robot no), mixed-key or(), child traversals both directions, point lookups, SEARCH + negative label,
      element-changing steps after a negative label — compared as id sets
  P   execution plan: the final HugeGraphStep / HugeVertexStep line of explain() for the key shapes — compared as a
      one-element "id set" (the plan text), so master vs PR plan changes show up as MISMATCH on the version axis
  G   Gremlin ~page paging with a downstream negative label: union of all pages == unpaged set, page count as scalar

usage: python3 hg_j8.py run --port 8081 --out j8_oracle.json
       python3 hg_j8.py run --port 8080 --out j8_hstore.json --expect j8_oracle.json
       python3 hg_suite.py compare j8_oracle.json j8_hstore.json --ids
"""
import argparse, json, re, sys, time
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from hg_suite import HG, Report

PAGE_HELPER = "org.apache.hugegraph.traversal.optimize.TraversalUtil"


def run_J8(hg, rep):
    def R(name, g): rep.case(name, *hg.ids_grem(g))
    A_ = "g.V('a').outE()"
    rep.sec("J8", "label predicates in the #2994 review shapes")
    print("--- J8a. vertices: indexed property, then negative label behind a barrier-like step (age indexed on both labels)")
    base = "g.V().has('age',gte(60))"
    R("V age>=60 neq(person)", base + ".hasLabel(neq('person')).id()")
    for step in ("skip(0)", "limit(100000)", "range(0,100000)", "aggregate('x')", "coin(1.0)", "dedup()", "order().by(id)",
                 "barrier()", "sideEffect(identity())", "identity()"):
        R(f"V age>=60 {step} neq(person)", f"{base}.{step}.hasLabel(neq('person')).id()")
    R("V age>=60 limit(5) neq(person) (limit before label, may cut)", base + ".order().by(id).limit(5).hasLabel(neq('person')).id()")
    R("V age>=60 skip(10) neq(person) (skip before label)", base + ".order().by(id).skip(10).hasLabel(neq('person')).id()")
    print("--- J8b. property indexed on person only (score): negative label must not lose robots")
    base = "g.V().has('score',gte(40))"
    R("V score>=40 (both labels)", base + ".id()")
    R("V score>=40 neq(person)", base + ".hasLabel(neq('person')).id()")
    R("V score>=40 without(person)", base + ".hasLabel(without('person')).id()")
    R("V neq(person) score>=40", "g.V().hasLabel(neq('person')).has('score',gte(40)).id()")
    R("V robot score>=40 (positive control)", "g.V().hasLabel('robot').has('score',gte(40)).id()")
    for step in ("limit(100000)", "skip(0)", "range(0,100000)", "aggregate('x')", "coin(1.0)", "barrier()", "dedup()", "order().by(id)"):
        R(f"V score>=40 {step} neq(person)", f"{base}.{step}.hasLabel(neq('person')).id()")
    R("V score>=40 age>=30 neq(person)", base + ".has('age',gte(30)).hasLabel(neq('person')).id()")
    R("V score>=40 limit neq(person) age>=30", base + ".limit(100000).hasLabel(neq('person')).has('age',gte(30)).id()")
    R("V cnt=5 neq(person) (cnt unindexed everywhere)", "g.V().has('cnt',5).hasLabel(neq('person')).id()")
    R("V cnt=5 limit neq(person)", "g.V().has('cnt',5).limit(100000).hasLabel(neq('person')).id()")
    print("--- J8c. mixed-key or()/and()/not() with different index coverage")
    R("V or(neq(person), score>=49)", "g.V().or(hasLabel(neq('person')), has('score',gte(49))).id()")
    R("V or(score>=49, neq(person))", "g.V().or(has('score',gte(49)), hasLabel(neq('person'))).id()")
    R("V age>=30 or(neq(person), cnt=5)", "g.V().has('age',gte(30)).or(hasLabel(neq('person')), has('cnt',5)).id()")
    R("V score>=40 or(neq(person), cnt=5)", "g.V().has('score',gte(40)).or(hasLabel(neq('person')), has('cnt',5)).id()")
    R("V and(score>=40, neq(person))", "g.V().and(has('score',gte(40)), hasLabel(neq('person'))).id()")
    R("V score>=40 not(hasLabel(person))", "g.V().has('score',gte(40)).not(hasLabel('person')).id()")
    R("V score>=40 limit not(hasLabel(person))", "g.V().has('score',gte(40)).limit(100000).not(hasLabel('person')).id()")
    R("V or(and(score>=40,neq(person)), age=18)", "g.V().or(and(has('score',gte(40)), hasLabel(neq('person'))), has('age',18)).id()")
    R("V not(or(hasLabel(person), age<30)) score>=40", "g.V().not(or(hasLabel('person'), has('age',lt(30)))).has('score',gte(40)).id()")
    print("--- J8d. child traversals: negative label in a child after a pushed-down property, and the mirror image")
    R("V score>=40 where(not(hasLabel(person)))", "g.V().has('score',gte(40)).where(__.not(__.hasLabel('person'))).id()")
    R("V score>=40 filter(hasLabel(neq(person)))", "g.V().has('score',gte(40)).filter(__.hasLabel(neq('person'))).id()")
    R("V score>=40 limit where(not(hasLabel(person)))", "g.V().has('score',gte(40)).limit(100000).where(__.not(__.hasLabel('person'))).id()")
    R("V score>=40 where(hasLabel(neq(person)))", "g.V().has('score',gte(40)).where(__.hasLabel(neq('person'))).id()")
    R("V score>=40 choose(hasLabel(person), fail, ok)", "g.V().has('score',gte(40)).choose(__.hasLabel('person'), __.limit(0), __.identity()).id()")
    R("mirror: union(V score>=40) neq(person)", "g.V('a').union(__.V().has('score',gte(40))).hasLabel(neq('person')).id()")
    R("mirror: flatMap(V score>=40) neq(person)", "g.inject(1).flatMap(__.V().has('score',gte(40))).hasLabel(neq('person')).id()")
    R("mirror: union(V score>=40) limit neq(person)", "g.V('a').union(__.V().has('score',gte(40))).limit(100000).hasLabel(neq('person')).id()")
    R("mirror: union(V age>=60) neq(person)", "g.V('a').union(__.V().has('age',gte(60))).hasLabel(neq('person')).id()")
    R("mirror: repeat(V score>=40).times(1) neq(person)", "g.V('a').repeat(__.V().has('score',gte(40))).times(1).hasLabel(neq('person')).id()")
    R("mirror: union(V score>=40) where(not(hasLabel(person)))", "g.V('a').union(__.V().has('score',gte(40))).where(__.not(__.hasLabel('person'))).id()")
    print("--- J8e. point lookups with a downstream negative label (must stay point lookups)")
    R("V(p00010) neq(robot)", "g.V('p00010').hasLabel(neq('robot')).id()")
    R("V(p00010) limit(10) neq(robot)", "g.V('p00010').limit(10).hasLabel(neq('robot')).id()")
    R("V(p00010,r0001) limit(10) neq(person)", "g.V('p00010','r0001').limit(10).hasLabel(neq('person')).id()")
    R("V hasId(p00010) limit(10) neq(robot)", "g.V().hasId('p00010').limit(10).hasLabel(neq('robot')).id()")
    R("V hasId(within(p00010,r0001)) neq(person)", "g.V().hasId(within('p00010','r0001')).hasLabel(neq('person')).id()")
    R("V(p00010) limit(10) neq(person) (-> empty)", "g.V('p00010').limit(10).hasLabel(neq('person')).id()")
    R("V(p00010) aggregate neq(robot)", "g.V('p00010').aggregate('x').hasLabel(neq('robot')).id()")
    R("V(p00010) age>=18 limit neq(robot)", "g.V('p00010').has('age',gte(18)).limit(10).hasLabel(neq('robot')).id()")
    R("E(a->b flow ETC) via outE limit neq(dummy1)", "g.V('a').outE('flow').has('asset','ETC').limit(100000).hasLabel(neq('dummy1')).id()")
    print("--- J8f. SEARCH index + negative label (firm: fname SEARCH)")
    R("V fname contains(gold) neq(person)", "g.V().has('fname',Text.contains('gold')).hasLabel(neq('person')).id()")
    R("V fname contains(gold) limit neq(person)", "g.V().has('fname',Text.contains('gold')).limit(100000).hasLabel(neq('person')).id()")
    R("V fname contains(gold) aggregate neq(person)", "g.V().has('fname',Text.contains('gold')).aggregate('x').hasLabel(neq('person')).id()")
    R("V fname contains(gold) limit neq(firm) (-> empty)", "g.V().has('fname',Text.contains('gold')).limit(100000).hasLabel(neq('firm')).id()")
    R("V fname contains((gold|silver)) limit neq(person)", "g.V().has('fname',Text.contains('(gold|silver)')).limit(100000).hasLabel(neq('person')).id()")
    R("V fname contains(诚信) limit neq(person)", "g.V().has('fname',Text.contains('诚信')).limit(100000).hasLabel(neq('person')).id()")
    R("V neq(person) fname contains(gold)", "g.V().hasLabel(neq('person')).has('fname',Text.contains('gold')).id()")
    R("V type>=2 contains(gold) limit neq(person)", "g.V().has('type',gte(2)).has('fname',Text.contains('gold')).limit(100000).hasLabel(neq('person')).id()")
    R("V firm contains(gold) (positive control)", "g.V().hasLabel('firm').has('fname',Text.contains('gold')).id()")
    print("--- J8g. element-changing steps after / before a negative label (the cost shape; sets must still agree)")
    R("V age>=60 out() neq(person)", "g.V().has('age',gte(60)).out().hasLabel(neq('person')).id()")
    R("V age>=60 out() where(not(hasLabel(person)))", "g.V().has('age',gte(60)).out().where(__.not(__.hasLabel('person'))).id()")
    R("V node out() neq(nnode)", "g.V().hasLabel('node').out().hasLabel(neq('nnode')).id()")
    R("V(a) outE asset=ETC inV neq(person)", "g.V('a').outE().has('asset','ETC').inV().hasLabel(neq('person')).id()")
    R("V(a) out() neq(person) in() neq(person)", "g.V('a').out().hasLabel(neq('person')).in().hasLabel(neq('person')).id()")
    R("V firm type>=2 out(deal) neq(person)", "g.V().hasLabel('firm').has('type',gte(2)).out('deal').hasLabel(neq('person')).id()")
    R("V type>=2 outE(deal) neq(flow) inV", "g.V().has('type',gte(2)).outE('deal').hasLabel(neq('flow')).inV().id()")
    R("V age>=60 neq(person) both() dedup", "g.V().has('age',gte(60)).hasLabel(neq('person')).both().dedup().id()")
    R("V(a) outE has(epoch>=30) inV out() neq(person)", "g.V('a').outE().has('epoch',gte(30)).inV().out().hasLabel(neq('person')).id()")
    print("--- J8h. edges: sort-key / property then negative label behind barrier-like steps")
    for step in ("limit(100000)", "skip(0)", "range(0,100000)", "aggregate('x')", "coin(1.0)", "dedup()", "order().by(id)"):
        R(f"outE asset=ETC {step} neq(dummy3)", f"{A_}.has('asset','ETC').{step}.hasLabel(neq('dummy3')).id()")
    R("outE epoch>=30 limit without(flow)", A_ + ".has('epoch',gte(30)).limit(100000).hasLabel(without('flow')).id()")
    R("outE amount>=100 limit neq(flow) (amount only on dummy*)", A_ + ".has('amount',gte(100)).limit(100000).hasLabel(neq('flow')).id()")
    R("outE amount>=100 neq(dummy1)", A_ + ".has('amount',gte(100)).hasLabel(neq('dummy1')).id()")
    R("outE or(neq(flow), amount>=300)", A_ + ".or(hasLabel(neq('flow')), has('amount',gte(300))).id()")
    R("outE where(not(hasLabel(flow))) asset=ETC", A_ + ".where(__.not(__.hasLabel('flow'))).has('asset','ETC').id()")
    R("outE asset=ETC where(not(hasLabel(flow)))", A_ + ".has('asset','ETC').where(__.not(__.hasLabel('flow'))).id()")
    R("E() asset=ETC neq(flow) (global edge scan)", "g.E().has('asset','ETC').hasLabel(neq('flow')).id()")
    R("E() amt>=500 limit neq(flow) (deal range index)", "g.E().has('amt',gte(500)).limit(100000).hasLabel(neq('flow')).id()")
    R("E() amt>=500 neq(deal) (-> empty)", "g.E().has('amt',gte(500)).hasLabel(neq('deal')).id()")
    print("--- J8i. hasKey / hasValue / connective ids next to a negative label (review 2026-09-07, head fefe3ca)")
    R("V hasKey(age) (control)", "g.V().hasKey('age').id()")
    R("V hasKey(age) neq(person)", "g.V().hasKey('age').hasLabel(neq('person')).id()")
    R("V hasKey(age) limit neq(person)", "g.V().hasKey('age').limit(100000).hasLabel(neq('person')).id()")
    R("V neq(person) hasKey(age)", "g.V().hasLabel(neq('person')).hasKey('age').id()")
    R("V hasKey(score) neq(robot)", "g.V().hasKey('score').hasLabel(neq('robot')).id()")
    R("V hasKey(fname) neq(person)", "g.V().hasKey('fname').hasLabel(neq('person')).id()")
    R("V hasKey(age,score) without(person)", "g.V().hasKey('age','score').hasLabel(without('person')).id()")
    R("V hasKey(nope) neq(person) (-> empty)", "g.V().hasKey('nope').hasLabel(neq('person')).id()")
    R("V hasValue(20) (control)", "g.V().hasValue(20).id()")
    R("V hasValue(20) neq(person)", "g.V().hasValue(20).hasLabel(neq('person')).id()")
    R("V hasValue(20) limit neq(person)", "g.V().hasValue(20).limit(100000).hasLabel(neq('person')).id()")
    R("V hasKey(age) hasValue(20) neq(person)", "g.V().hasKey('age').hasValue(20).hasLabel(neq('person')).id()")
    R("V hasKey(age) age>=60 neq(person)", "g.V().hasKey('age').has('age',gte(60)).hasLabel(neq('person')).id()")
    R("V hasKey(age) where(not(hasLabel(person)))", "g.V().hasKey('age').where(__.not(__.hasLabel('person'))).id()")
    R("V firm out(deal) hasKey(type) neq(person)", "g.V().hasLabel('firm').out('deal').hasKey('type').hasLabel(neq('person')).id()")
    R("V firm out(deal) hasKey(type) neq(firm) (-> empty)", "g.V().hasLabel('firm').out('deal').hasKey('type').hasLabel(neq('firm')).id()")
    R("outE hasKey(amount) neq(flow)", A_ + ".hasKey('amount').hasLabel(neq('flow')).id()")
    R("outE hasKey(amount) limit neq(dummy1)", A_ + ".hasKey('amount').limit(100000).hasLabel(neq('dummy1')).id()")
    R("outE hasValue(ETC) neq(flow)", A_ + ".hasValue('ETC').hasLabel(neq('flow')).id()")
    R("outE hasValue(ETC) limit neq(dummy3)", A_ + ".hasValue('ETC').limit(100000).hasLabel(neq('dummy3')).id()")
    R("E() hasKey(amt) neq(flow)", "g.E().hasKey('amt').hasLabel(neq('flow')).id()")
    R("E() hasKey(amt) neq(deal) (-> empty)", "g.E().hasKey('amt').hasLabel(neq('deal')).id()")
    R("V hasId(within(p00010,r0001).and(neq(r0001))) limit neq(robot)", "g.V().hasId(within('p00010','r0001').and(neq('r0001'))).limit(10).hasLabel(neq('robot')).id()")
    R("V hasId(eq(p00010).and(neq(r0001))) limit neq(robot)", "g.V().hasId(eq('p00010').and(neq('r0001'))).limit(10).hasLabel(neq('robot')).id()")
    R("V hasId(within(p00010,r0001).and(neq(r0001))) neq(person) (-> empty)", "g.V().hasId(within('p00010','r0001').and(neq('r0001'))).hasLabel(neq('person')).id()")
    R("V(p00010,r0001) hasId(within(p00010,r0001)) limit neq(person)", "g.V('p00010','r0001').hasId(within('p00010','r0001')).limit(10).hasLabel(neq('person')).id()")
    R("V hasId(within(p00010,r0001).or(eq(f0001))) limit neq(person)", "g.V().hasId(within('p00010','r0001').or(eq('f0001'))).limit(10).hasLabel(neq('person')).id()")
    print("--- J8j. positive label + unsafe label in a child traversal; explicit-term SEARCH in a child (review 2026-09-08, head 2d53a55)")
    R("V person where(out().neq(robot)) (-> empty, persons have no edges)", "g.V().hasLabel('person').where(__.out().hasLabel(neq('robot'))).id()")
    R("V firm where(out(deal).neq(person))", "g.V().hasLabel('firm').where(__.out('deal').hasLabel(neq('person'))).id()")
    R("V firm where(out(deal).neq(firm)) (-> empty)", "g.V().hasLabel('firm').where(__.out('deal').hasLabel(neq('firm'))).id()")
    R("V within(firm,robot) where(out().neq(person))", "g.V().hasLabel(within('firm','robot')).where(__.out().hasLabel(neq('person'))).id()")
    R("V firm type>=2 where(out(deal).neq(person))", "g.V().hasLabel('firm').has('type',gte(2)).where(__.out('deal').hasLabel(neq('person'))).id()")
    R("V node where(outE().neq(flow))", "g.V().hasLabel('node').where(__.outE().hasLabel(neq('flow'))).id()")
    R("V node where(not(outE().hasLabel(flow)))", "g.V().hasLabel('node').where(__.not(__.outE().hasLabel('flow'))).id()")
    R("V firm where(out(deal).neq(person)) limit(50) order id", "g.V().hasLabel('firm').where(__.out('deal').hasLabel(neq('person'))).order().by(id).limit(50).id()")
    R("V firm AND robot where(out().neq(person)) (conflict -> empty)", "g.V().hasLabel('firm').hasLabel('robot').where(__.out().hasLabel(neq('person'))).id()")
    R("V within() where(out().neq(person)) (-> empty)", "g.V().hasLabel(within()).where(__.out().hasLabel(neq('person'))).id()")
    R("V nope where(out().neq(person)) (missing label)", "g.V().hasLabel('nope').where(__.out().hasLabel(neq('person'))).id()")
    R("E deal where(inV().neq(person))", "g.E().hasLabel('deal').where(__.inV().hasLabel(neq('person'))).id()")
    R("V firm contains((gold)) limit neq(person) (explicit term)", "g.V().hasLabel('firm').has('fname',Text.contains('(gold)')).limit(100000).hasLabel(neq('person')).id()")
    R("V contains((gold)) (control)", "g.V().has('fname',Text.contains('(gold)')).id()")
    R("V firm where(out(deal).has(fname,contains((gold))).neq(person))", "g.V().hasLabel('firm').where(__.out('deal').has('fname',Text.contains('(gold)')).hasLabel(neq('person'))).id()")
    R("V firm where(out(deal).has(fname,contains(gold)).neq(person))", "g.V().hasLabel('firm').where(__.out('deal').has('fname',Text.contains('gold')).hasLabel(neq('person'))).id()")
    R("V firm where(out(deal).has(fname,contains((gold|silver))).neq(person))", "g.V().hasLabel('firm').where(__.out('deal').has('fname',Text.contains('(gold|silver)')).hasLabel(neq('person'))).id()")
    R("V(f0001) out(deal) contains((gold)) neq(person)", "g.V('f0001').out('deal').has('fname',Text.contains('(gold)')).hasLabel(neq('person')).id()")


PLAN_SHAPES = [
    ("V(p00010) limit(10) neq(robot)", "g.V('p00010').limit(10).hasLabel(neq('robot'))"),
    ("V hasId(p00010) limit(10) neq(robot)", "g.V().hasId('p00010').limit(10).hasLabel(neq('robot'))"),
    ("V(p00010) neq(robot)", "g.V('p00010').hasLabel(neq('robot'))"),
    ("V(p00010) (control)", "g.V('p00010')"),
    ("V age>=60 neq(person)", "g.V().has('age',gte(60)).hasLabel(neq('person'))"),
    ("V age>=60 limit neq(person)", "g.V().has('age',gte(60)).limit(100000).hasLabel(neq('person'))"),
    ("V age>=60 aggregate neq(person)", "g.V().has('age',gte(60)).aggregate('x').hasLabel(neq('person'))"),
    ("V score>=40 neq(person)", "g.V().has('score',gte(40)).hasLabel(neq('person'))"),
    ("V score>=40 limit neq(person)", "g.V().has('score',gte(40)).limit(100000).hasLabel(neq('person'))"),
    ("V age>=60 (control)", "g.V().has('age',gte(60))"),
    ("V age>=60 out() neq(person)", "g.V().has('age',gte(60)).out().hasLabel(neq('person'))"),
    ("V age>=60 out() where(not(hasLabel(person)))", "g.V().has('age',gte(60)).out().where(__.not(__.hasLabel('person')))"),
    ("V age>=60 out() (control)", "g.V().has('age',gte(60)).out()"),
    ("V fname contains(gold) limit neq(person)", "g.V().has('fname',Text.contains('gold')).limit(100000).hasLabel(neq('person'))"),
    ("V fname contains(gold) (control)", "g.V().has('fname',Text.contains('gold'))"),
    ("V score>=40 where(not(hasLabel(person)))", "g.V().has('score',gte(40)).where(__.not(__.hasLabel('person')))"),
    ("union(V score>=40) neq(person)", "g.V('a').union(__.V().has('score',gte(40))).hasLabel(neq('person'))"),
    ("V(a) outE asset=ETC limit neq(dummy3)", "g.V('a').outE().has('asset','ETC').limit(100000).hasLabel(neq('dummy3'))"),
    ("V(a) outE asset=ETC (control)", "g.V('a').outE().has('asset','ETC')"),
    ("V age>=60 has(~page) limit(50) neq(person)", "g.V().has('~page','').has('age',gte(60)).limit(50).hasLabel(neq('person'))"),
    ("V hasKey(age) neq(person)", "g.V().hasKey('age').hasLabel(neq('person'))"),
    ("V hasKey(age) (control)", "g.V().hasKey('age')"),
    ("V hasId(within(p00010,r0001).and(neq(r0001))) limit neq(robot)", "g.V().hasId(within('p00010','r0001').and(neq('r0001'))).limit(10).hasLabel(neq('robot'))"),
    ("V firm where(out(deal).neq(person))", "g.V().hasLabel('firm').where(__.out('deal').hasLabel(neq('person')))"),
    ("V within(firm,robot) where(out().neq(person))", "g.V().hasLabel(within('firm','robot')).where(__.out().hasLabel(neq('person')))"),
    ("V firm type>=2 where(out(deal).neq(person))", "g.V().hasLabel('firm').has('type',gte(2)).where(__.out('deal').hasLabel(neq('person')))"),
    ("V firm where(out(deal).contains((gold)).neq(person))", "g.V().hasLabel('firm').where(__.out('deal').has('fname',Text.contains('(gold)')).hasLabel(neq('person')))"),
]


def final_plan(hg, g):
    """Final traversal of explain(): the list of steps after all strategies, as one normalised string."""
    st, d = hg.gremlin(g + ".explain().toString()")
    if HG._bad(st, d): return None, HG._err(d)
    data = d.get("result", {}).get("data", []) or []
    txt = data[0] if data else ""
    if not isinstance(txt, str): txt = json.dumps(txt, ensure_ascii=False)
    lines = txt.split("\n")
    fin = [l for l in lines if l.lstrip().startswith("Final Traversal")]
    line = fin[-1] if fin else lines[-1]
    line = re.sub(r"^\s*Final Traversal\s*", "", line).strip()
    line = re.sub(r"@[0-9a-f]{4,}", "", line)          # object identity hashes
    return [line], None


def run_P(hg, rep):
    rep.sec("P", "execution plan (explain(): final traversal) for the review shapes")
    for name, g in PLAN_SHAPES:
        ids, err = final_plan(hg, g)
        rep.case("PLAN " + name, ids, err)
        if ids: print("      " + ids[0][:300])


def page_all(hg, body, size, max_pages=2000):
    """Gremlin ~page loop: body is a traversal *without* g.V() start-page; returns (ids, pages, err)."""
    ids, page, pages = [], "", 0
    while True:
        script = (f"t = {body.replace('PAGE', json.dumps(page))}.limit({size}); r = t.toList().collect{{ it.id() }}; "
                  f"p = {PAGE_HELPER}.page(t); [r, p]")
        st, d = hg.gremlin(script)
        if HG._bad(st, d): return None, pages, HG._err(d)
        data = d.get("result", {}).get("data", []) or []
        if len(data) != 2: return None, pages, f"unexpected page result {str(data)[:120]}"
        r, p = data
        ids += [x["id"] if isinstance(x, dict) else x for x in (r or [])]
        pages += 1
        if not p or pages >= max_pages: break
        page = p
    return ids, pages, None


def run_G(hg, rep):
    rep.sec("G", "~page paging with a downstream negative label (union of pages vs unpaged)")
    shapes = [
        ("age>=60 neq(person)", "g.V().has('~page',PAGE).has('age',gte(60)).hasLabel(neq('person'))", "g.V().has('age',gte(60)).hasLabel(neq('person')).id()"),
        ("score>=40 neq(person)", "g.V().has('~page',PAGE).has('score',gte(40)).hasLabel(neq('person'))", "g.V().has('score',gte(40)).hasLabel(neq('person')).id()"),
        ("score>=40 (control, no label)", "g.V().has('~page',PAGE).has('score',gte(40))", "g.V().has('score',gte(40)).id()"),
        ("robot age>=60 (control, positive label)", "g.V().has('~page',PAGE).hasLabel('robot').has('age',gte(60))", "g.V().hasLabel('robot').has('age',gte(60)).id()"),
        ("neq(person) age>=60", "g.V().has('~page',PAGE).hasLabel(neq('person')).has('age',gte(60))", "g.V().hasLabel(neq('person')).has('age',gte(60)).id()"),
        ("fname contains(gold) neq(person)", "g.V().has('~page',PAGE).has('fname',Text.contains('gold')).hasLabel(neq('person'))", "g.V().has('fname',Text.contains('gold')).hasLabel(neq('person')).id()"),
    ]
    for name, body, unpaged in shapes:
        rep.case(f"PAGE unpaged {name}", *hg.ids_grem(unpaged))
        for size in (7, 50, 500):
            ids, pages, err = page_all(hg, body, size)
            rep.case(f"PAGE size={size} {name}", ids, err)
            rep.scalar(f"PAGE size={size} {name} pages", pages if ids is not None else -1)


SECTIONS = {"J8": run_J8, "P": run_P, "G": run_G}


def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--host", default="localhost"); r.add_argument("--port", type=int, required=True)
    r.add_argument("--graph", default="hugegraph"); r.add_argument("--space", default="DEFAULT")
    r.add_argument("--sections", default="J8,P,G"); r.add_argument("--out"); r.add_argument("--expect")
    a = ap.parse_args()
    hg = HG(a.host, a.port, a.space, a.graph)
    info = hg.info(); print("server:", info)
    rep = Report(a.expect)
    t0 = time.time()
    for s in a.sections.split(","):
        SECTIONS[s](hg, rep)
    if a.expect:
        print("\nTALLY:", " ".join(f"{k}={v}" for k, v in sorted(rep.tally.items())))
    print(f"cases={len(rep.cases)} time={time.time() - t0:.0f}s")
    if a.out: rep.dump(a.out, dict(info, port=a.port, sections=a.sections, suite="hg_j8"))


if __name__ == "__main__":
    main()
