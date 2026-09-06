#!/usr/bin/env python3
"""scale_probe.py — load N 'big' vertices (no property index) into the graph on --port, then time the point-lookup
and index-lookup shapes from the #2994 review with a downstream negative label. Prints ms, n, error, and the plan.
usage: python3 scale_probe.py --port 8081 --n 1000000 [--skip-load]"""
import argparse, json, sys, time
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from hg_suite import HG
from hg_j8 import final_plan

def load(hg, n):
    for pk in ({"name": "k", "data_type": "INT"},):
        st, r = hg.post("/schema/propertykeys", dict(pk, cardinality="SINGLE")); hg.wait_task(r)
    st, r = hg.post("/schema/vertexlabels", {"name": "big", "id_strategy": "CUSTOMIZE_STRING", "properties": ["k"],
                                            "nullable_keys": [], "enable_label_index": True}); print("  vertexlabel big:", st)
    st, r = hg.post("/schema/vertexlabels", {"name": "mark", "id_strategy": "CUSTOMIZE_STRING", "properties": ["k"],
                                            "nullable_keys": [], "enable_label_index": True}); print("  vertexlabel mark:", st)
    hg.post("/graph/vertices/batch", [{"label": "mark", "id": "mark0", "properties": {"k": 0}}])
    t0 = time.time(); B = 1000
    for s in range(0, n, B):
        batch = [{"label": "big", "id": f"big{i:08d}", "properties": {"k": i % 1000}} for i in range(s, min(n, s + B))]
        st, r = hg.post("/graph/vertices/batch", batch)
        if st not in (200, 201): print("  batch err:", s, st, str(r)[:120]); break
        if (s // B) % 100 == 0: print(f"  {s + len(batch)}/{n} {time.time() - t0:.0f}s", flush=True)
    print(f"  load done {time.time() - t0:.0f}s")

def timed(hg, g, repeat=3):
    best, n, err = None, None, None
    for _ in range(repeat):
        t0 = time.time(); ids, e = hg.ids_grem(g); ms = (time.time() - t0) * 1000
        if e: err = e; n = None; best = ms if best is None else min(best, ms); continue
        n = len(ids); best = ms if best is None else min(best, ms)
    return best, n, err

QUERIES = [
    ("point: V(big10) (control)", "g.V('big00000010').id()"),
    ("point: V(big10) neq(mark)", "g.V('big00000010').hasLabel(neq('mark')).id()"),
    ("point: V(big10) limit(10) neq(mark)", "g.V('big00000010').limit(10).hasLabel(neq('mark')).id()"),
    ("point: V hasId(big10) limit(10) neq(mark)", "g.V().hasId('big00000010').limit(10).hasLabel(neq('mark')).id()"),
    ("point: V(big10) limit(10) neq(big) (-> empty)", "g.V('big00000010').limit(10).hasLabel(neq('big')).id()"),
    ("point: V(big10,mark0) limit(10) neq(big)", "g.V('big00000010','mark0').limit(10).hasLabel(neq('big')).id()"),
    ("index: V age>=60 (control)", "g.V().has('age',gte(60)).count()"),
    ("index: V age>=60 limit neq(person)", "g.V().has('age',gte(60)).limit(100000).hasLabel(neq('person')).count()"),
    ("index: V age>=60 neq(person)", "g.V().has('age',gte(60)).hasLabel(neq('person')).count()"),
    ("index: V age>=60 out() neq(person)", "g.V().has('age',gte(60)).out().hasLabel(neq('person')).count()"),
    ("index: V age>=60 out() where(not(hasLabel(person)))", "g.V().has('age',gte(60)).out().where(__.not(__.hasLabel('person'))).count()"),
    ("index: V age>=60 out() (control)", "g.V().has('age',gte(60)).out().count()"),
    ("index: V score>=40 limit neq(person)", "g.V().has('score',gte(40)).limit(100000).hasLabel(neq('person')).count()"),
    ("index: V fname contains(gold) limit neq(person)", "g.V().has('fname',Text.contains('gold')).limit(100000).hasLabel(neq('person')).count()"),
    ("scan: V mark (label, control)", "g.V().hasLabel('mark').count()"),
]

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, required=True); ap.add_argument("--n", type=int, default=1000000)
    ap.add_argument("--skip-load", action="store_true"); ap.add_argument("--out")
    a = ap.parse_args(); hg = HG("localhost", a.port, "DEFAULT", "hugegraph"); print("server:", hg.info())
    if not a.skip_load: load(hg, a.n)
    print("total V:", hg.ids_grem("g.V().count()")[0])
    rows = []
    for name, g in QUERIES:
        ms, n, err = timed(hg, g)
        plan, perr = final_plan(hg, g.rsplit(".", 1)[0])
        row = {"name": name, "ms": round(ms), "n": n, "err": err, "plan": (plan or [perr])[0]}
        rows.append(row); print(f"{name:52} {row['ms']:>8} ms  n={n}  {('ERR ' + err) if err else ''}\n{'':52}   {row['plan'][:200]}", flush=True)
    if a.out: json.dump(rows, open(a.out, "w"), ensure_ascii=False, indent=1)

if __name__ == "__main__": main()
