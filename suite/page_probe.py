#!/usr/bin/env python3
"""Sonda paging: ktore id sie duplikuja i na ktorej granicy strony. Usage: page_probe.py --port 8081"""
import argparse, json, sys
sys.path.insert(0, "/home/seba")
from hg_suite import HG

ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, required=True); A = ap.parse_args()
hg = HG("localhost", A.port, "DEFAULT", "hugegraph")
V = {"vertex_id": '"a"', "direction": "OUT", "label": "flow"}

def walk(params, size):
    pages, page = [], ""
    while True:
        r, e, nxt = hg.ids_rest("/graph/edges", "edges", params, limit=size, page=page)
        if r is None: return None, e
        pages.append(r)
        if not nxt or len(pages) > 200: break
        page = nxt
    return pages, None

for label, props in (("prefix asset=ETC", {"asset": "ETC"}), ("range asset=ETC epoch>=150", {"asset": "ETC", "epoch": "P.gte(150)"}),
                     ("no-condition", None)):
    for size in (100, 250, 333, 400, 500, 600, 1000):
        params = dict(V)
        if props: params["properties"] = json.dumps(props)
        pages, e = walk(params, size)
        if pages is None: print(f"{label:30} size={size:5} ERR {e}"); continue
        flat = [x for p in pages for x in p]
        seen, dups = set(), []
        for i, x in enumerate(flat):
            if x in seen: dups.append((i, x))
            seen.add(x)
        bound = []
        for k in range(len(pages) - 1):
            if pages[k] and pages[k + 1] and pages[k][-1] == pages[k + 1][0]: bound.append(k)
        print(f"{label:30} size={size:5} pages={len(pages):3} sizes={[len(p) for p in pages][:6]}{'...' if len(pages) > 6 else ''} "
              f"n={len(flat)} uniq={len(seen)} dups={len(dups)} last==first@{bound} dup_pos={[i for i, _ in dups][:4]} dup_ids={[x[:40] for _, x in dups][:2]}")
