#!/usr/bin/env python3
"""idx_bench.py <variant: index|noindex> <edges> [threads=4] [batch=500] [vertices=100000] [out.json]
Creates the schema for the variant on a FRESH graph, loads the vertices, then loads the edges with N writer threads
(POST /graph/edges/batch, check_vertex=false) and reports throughput and batch latency percentiles."""
import json, os, random, sys, threading, time, urllib.request, urllib.error
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "suite")); sys.path.insert(0, os.path.expanduser("~")); from hg_suite import HG
V, E, T, B, NV = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else 4, int(sys.argv[4]) if len(sys.argv) > 4 else 500, int(sys.argv[5]) if len(sys.argv) > 5 else 100000
OUT = sys.argv[6] if len(sys.argv) > 6 else None
hg = HG("localhost", 8080, "DEFAULT", "hugegraph"); print("server:", hg.info(), "variant:", V, flush=True)
def task(r):
    try: return json.loads(r)
    except Exception: return {}
def post(path, body, timeout=600):
    req = urllib.request.Request(hg.base + path, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return r.status, r.read()
    except urllib.error.HTTPError as e: return e.code, e.read()
    except Exception as e: return 0, str(e)[:120].encode()
# schema
for pk in ({"name": "addr", "data_type": "TEXT"}, {"name": "ts", "data_type": "LONG"}, {"name": "amount", "data_type": "DOUBLE"}):
    st, r = post("/schema/propertykeys", dict(pk, cardinality="SINGLE")); hg.wait_task(task(r))
st, r = post("/schema/vertexlabels", {"name": "acct", "id_strategy": "CUSTOMIZE_STRING", "properties": [], "nullable_keys": [], "enable_label_index": True}); print("  vertexlabel acct:", st)
st, r = post("/schema/edgelabels", {"name": "flow", "source_label": "acct", "target_label": "acct", "frequency": "MULTIPLE", "sort_keys": ["addr", "ts"], "properties": ["addr", "ts", "amount"], "nullable_keys": [], "enable_label_index": V == "index"}); print("  edgelabel flow (label index=%s):" % (V == "index"), st, r[:100])
if V == "index":
    st, r = post("/schema/indexlabels", {"name": "flowByAddr", "base_type": "EDGE_LABEL", "base_value": "flow", "index_type": "SECONDARY", "fields": ["addr"]}); print("  indexlabel flowByAddr:", st, hg.wait_task(task(r)))
# vertices
t0 = time.time(); bad = 0
for s in range(0, NV, 1000):
    st, r = post("/graph/vertices/batch", [{"label": "acct", "id": f"a{i:06d}", "properties": {}} for i in range(s, min(NV, s + 1000))])
    if st not in (200, 201): bad += 1
print(f"  vertices: {NV} in {time.time()-t0:.1f}s, bad batches={bad}", flush=True)
# edges
rnd = random.Random(42); addrs = [f"{rnd.getrandbits(80):020x}" for _ in range(NV)]
lock = threading.Lock(); lat = []; errors = {}; done = [0]; next_start = [0]
def take():
    with lock:
        s = next_start[0]
        if s >= E: return None
        next_start[0] = s + B; return s
def writer(w):
    r = random.Random(1000 + w)
    while True:
        s = take()
        if s is None: return
        edges = []
        for i in range(s, min(E, s + B)):
            src = r.randrange(NV); dst = r.randrange(NV)
            edges.append({"label": "flow", "outV": f"a{src:06d}", "outVLabel": "acct", "inV": f"a{dst:06d}", "inVLabel": "acct",
                          "properties": {"addr": addrs[dst], "ts": 1700000000000 + i * 1000, "amount": round(r.random() * 1000, 3)}})
        t = time.time(); st, msg = post("/graph/edges/batch?check_vertex=false", edges); dt = time.time() - t
        with lock:
            lat.append(dt)
            if st in (200, 201): done[0] += len(edges)
            else: errors[f"{st} {msg[:60].decode(errors='replace') if isinstance(msg, bytes) else msg}"] = errors.get(f"{st} {msg[:60].decode(errors='replace') if isinstance(msg, bytes) else msg}", 0) + 1
            n = done[0]
        if (s // B) % 200 == 0: print(f"  {time.strftime('%H:%M:%S')} edges {n}/{E} rate={n/(time.time()-T0):.0f}/s p50={sorted(lat)[len(lat)//2]*1000:.0f}ms", flush=True)
T0 = time.time(); ts = [threading.Thread(target=writer, args=(w,)) for w in range(T)]; [t.start() for t in ts]; [t.join() for t in ts]; total = time.time() - T0
lat.sort(); p = lambda q: lat[min(len(lat)-1, int(q * len(lat)))] * 1000
res = {"variant": V, "edges": done[0], "requested": E, "threads": T, "batch": B, "vertices": NV, "seconds": round(total, 1), "edges_per_s": round(done[0] / total),
       "batch_ms": {"p50": round(p(0.5)), "p90": round(p(0.9)), "p99": round(p(0.99)), "max": round(lat[-1] * 1000)}, "errors": errors}
print("RESULT", json.dumps(res), flush=True)
if OUT: json.dump(res, open(OUT, "w"))
