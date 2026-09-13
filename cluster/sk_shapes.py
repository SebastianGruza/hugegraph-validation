#!/usr/bin/env python3
"""sk_shapes.py <port> [label] — sort-key query shapes on the sk4 schema (xfer[btc_addr, seq, height, asset]).
Picks one vertex with out-edges and one of its edges, then runs prefix/range shapes over the sort keys through
Gremlin and reports count or the error class per shape. Output: one JSON line per shape plus a summary."""
import gzip, json, os, sys, time, urllib.request, urllib.error
PORT = int(sys.argv[1]); LABEL = sys.argv[2] if len(sys.argv) > 2 else str(PORT); FIX = sys.argv[3] if len(sys.argv) > 3 else None  # optional vertex id; results as sorted id lists
URL = f"http://127.0.0.1:{PORT}/gremlin"; AL = {"graph": "DEFAULT-hugegraph", "g": "__g_DEFAULT-hugegraph"}
def q(g, timeout=120):
    req = urllib.request.Request(URL, data=json.dumps({"gremlin": g, "aliases": AL}).encode(), headers={"Content-Type": "application/json"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: b = r.read(); b = gzip.decompress(b) if b[:2] == b"\x1f\x8b" else b; d = json.loads(b); return d["result"]["data"], None, round((time.time() - t) * 1000)
    except urllib.error.HTTPError as e:
        b = e.read(); b = (gzip.decompress(b) if b[:2] == b"\x1f\x8b" else b).decode(errors="replace")
        try: m = json.loads(b).get("message") or json.loads(b).get("exception") or b
        except Exception: m = b
        return None, f"HTTP {e.code}: {m[:160]}", round((time.time() - t) * 1000)
    except Exception as e: return None, f"{type(e).__name__}: {str(e)[:120]}", round((time.time() - t) * 1000)
# pick a vertex with many out edges: scan a few vertices, take the first with >= 3 out edges
vs, err, _ = q(f"g.V('{FIX}').id()" if FIX else "g.V().limit(200).id()")
assert not err, err
vid = None; e0 = None
for v in vs:
    es, err, _ = q(f"g.V('{v}').outE('xfer').limit(50).valueMap()")
    if err: continue
    if len(es) >= 3:
        vid = v; e0 = es[0]; break
assert vid, "no vertex with out edges found"
def one(x): return x[0] if isinstance(x, list) else x
addr, seq, height, asset = one(e0["btc_addr"]), one(e0["seq"]), one(e0["height"]), one(e0["asset"])
print(json.dumps({"label": LABEL, "vertex": vid, "edge": {"btc_addr": addr, "seq": seq, "height": height, "asset": asset}}))
V = f"g.V('{vid}')"
shapes = [
  ("out no sort-key condition (control)",           f"{V}.outE('xfer').id()"),
  ("prefix 1: btc_addr",                              f"{V}.outE('xfer').has('btc_addr','{addr}').id()"),
  ("prefix 2: btc_addr, seq",                         f"{V}.outE('xfer').has('btc_addr','{addr}').has('seq',{seq}).id()"),
  ("prefix 1 + range on seq",                         f"{V}.outE('xfer').has('btc_addr','{addr}').has('seq',gte(1)).id()"),
  ("prefix 2 + range on height",                      f"{V}.outE('xfer').has('btc_addr','{addr}').has('seq',{seq}).has('height',between({height-10},{height+10})).id()"),
  ("prefix 3 + eq asset (all 4 keys)",                f"{V}.outE('xfer').has('btc_addr','{addr}').has('seq',{seq}).has('height',{height}).has('asset','{asset}').id()"),
  ("prefix 3 + range on asset",                       f"{V}.outE('xfer').has('btc_addr','{addr}').has('seq',{seq}).has('height',{height}).has('asset',gte('a')).id()"),
  ("non-prefix: seq only (filter, control)",          f"{V}.outE('xfer').has('seq',{seq}).id()"),
  ("non-sort-key prop: amount range (filter)",        f"{V}.outE('xfer').has('amount',gte(0)).id()"),
  ("prefix 1 + non-sort-key prop",                    f"{V}.outE('xfer').has('btc_addr','{addr}').has('amount',gte(0)).id()"),
  ("bothE prefix 1",                                  f"{V}.bothE('xfer').has('btc_addr','{addr}').id()"),
  ("inE prefix 1 on the target",                      f"g.V('{vid}').outE('xfer').limit(1).inV().inE('xfer').has('btc_addr','{addr}').id()"),
  ("prefix 1 + valueMap (rows decoded)",              f"{V}.outE('xfer').has('btc_addr','{addr}').limit(5).valueMap()"),
]
ok = bad = 0
for name, g in shapes:
    d, err, ms = q(g)
    if d is not None and isinstance(d, list): d = sorted(str(x) for x in d) if len(d) < 200 else ["n=%d" % len(d)]
    if err: bad += 1
    else: ok += 1
    print(json.dumps({"label": LABEL, "shape": name, "result": d, "error": err, "ms": ms}))
print(json.dumps({"label": LABEL, "summary": {"ok": ok, "failed": bad}}))
