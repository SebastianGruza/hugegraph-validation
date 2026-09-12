#!/usr/bin/env python3
"""idx_bench.py <variant: index|noindex|sk4-index|sk4-noindex> <edges> [threads=4] [batch=500] [vertices=100000] [out.json]
Creates the schema for the variant on a FRESH graph, loads the vertices, then loads the edges with N writer threads
(POST /graph/edges/batch, check_vertex=false) and reports throughput and batch latency percentiles.
sk4-* variants: vertex ids are BTC (base58, 34 chars) or ETH (0x + 40 hex) addresses; edge label `xfer` with four sort
keys [btc_addr (the counterparty address, Zipf-skewed so a few addresses are hot), seq 1..1000, height (block height,
~3000 edges per block), asset ("btc" or a 40-hex contract)]; index variant = label index + SECONDARY(btc_addr)."""
import json, os, random, sys, threading, time, urllib.request, urllib.error
sys.path.insert(0, os.path.expanduser("~")); from hg_suite import HG
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
SK4 = V.startswith("sk4"); IDX = V.endswith("index") and not V.endswith("noindex")
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
def btc_addr(r): return ("1" if r.random() < 0.7 else "3") + "".join(r.choice(B58) for _ in range(33))
def eth_addr(r): return "0x%040x" % r.getrandbits(160)
# schema
if SK4:
    for pk in ({"name": "btc_addr", "data_type": "TEXT"}, {"name": "seq", "data_type": "INT"}, {"name": "height", "data_type": "LONG"}, {"name": "asset", "data_type": "TEXT"}, {"name": "amount", "data_type": "DOUBLE"}):
        st, r = post("/schema/propertykeys", dict(pk, cardinality="SINGLE")); hg.wait_task(task(r))
    st, r = post("/schema/vertexlabels", {"name": "addr", "id_strategy": "CUSTOMIZE_STRING", "properties": [], "nullable_keys": [], "enable_label_index": True}); print("  vertexlabel addr:", st)
    st, r = post("/schema/edgelabels", {"name": "xfer", "source_label": "addr", "target_label": "addr", "frequency": "MULTIPLE", "sort_keys": ["btc_addr", "seq", "height", "asset"], "properties": ["btc_addr", "seq", "height", "asset", "amount"], "nullable_keys": [], "enable_label_index": IDX}); print("  edgelabel xfer (label index=%s):" % IDX, st, r[:100])
    if IDX:
        st, r = post("/schema/indexlabels", {"name": "xferByBtcAddr", "base_type": "EDGE_LABEL", "base_value": "xfer", "index_type": "SECONDARY", "fields": ["btc_addr"]}); print("  indexlabel xferByBtcAddr:", st, hg.wait_task(task(r)))
    VL, EL = "addr", "xfer"
else:
    for pk in ({"name": "addr", "data_type": "TEXT"}, {"name": "ts", "data_type": "LONG"}, {"name": "amount", "data_type": "DOUBLE"}):
        st, r = post("/schema/propertykeys", dict(pk, cardinality="SINGLE")); hg.wait_task(task(r))
    st, r = post("/schema/vertexlabels", {"name": "acct", "id_strategy": "CUSTOMIZE_STRING", "properties": [], "nullable_keys": [], "enable_label_index": True}); print("  vertexlabel acct:", st)
    st, r = post("/schema/edgelabels", {"name": "flow", "source_label": "acct", "target_label": "acct", "frequency": "MULTIPLE", "sort_keys": ["addr", "ts"], "properties": ["addr", "ts", "amount"], "nullable_keys": [], "enable_label_index": V == "index"}); print("  edgelabel flow (label index=%s):" % (V == "index"), st, r[:100])
    if V == "index":
        st, r = post("/schema/indexlabels", {"name": "flowByAddr", "base_type": "EDGE_LABEL", "base_value": "flow", "index_type": "SECONDARY", "fields": ["addr"]}); print("  indexlabel flowByAddr:", st, hg.wait_task(task(r)))
    VL, EL = "acct", "flow"
# vertices
rnd = random.Random(42)
if SK4:
    ids = [btc_addr(rnd) if i % 2 == 0 else eth_addr(rnd) for i in range(NV)]          # half BTC, half ETH-like
    # Zipf-like popularity of the counterparty: pick from a small hot set with p=0.5, else uniform
    hot = [i for i in range(0, NV, 2)][:max(1, NV // 1000)]                               # 0.1 % of vertices, BTC ones
    contracts = [eth_addr(rnd)[2:] for _ in range(50)]
else:
    ids = [f"a{i:06d}" for i in range(NV)]
t0 = time.time(); bad = 0
for s in range(0, NV, 1000):
    st, r = post("/graph/vertices/batch", [{"label": VL, "id": ids[i], "properties": {}} for i in range(s, min(NV, s + 1000))])
    if st not in (200, 201): bad += 1
print(f"  vertices: {NV} in {time.time()-t0:.1f}s, bad batches={bad}", flush=True)
# edges
addrs = [f"{rnd.getrandbits(80):020x}" for _ in range(NV)]
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
            src = r.randrange(NV)
            if SK4:
                h = r.random() < 0.5; dst = hot[r.randrange(len(hot))] if h else r.randrange(NV)
                edges.append({"label": EL, "outV": ids[src], "outVLabel": VL, "inV": ids[dst], "inVLabel": VL,
                              "properties": {"btc_addr": ids[dst] if dst % 2 == 0 else btc_addr(r), "seq": r.randint(1, 1000), "height": 800000 + i // 3000,
                                             "asset": "btc" if r.random() < 0.7 else contracts[r.randrange(50)], "amount": round(r.random() * 1000, 3)}})
            else:
                dst = r.randrange(NV)
                edges.append({"label": EL, "outV": ids[src], "outVLabel": VL, "inV": ids[dst], "inVLabel": VL,
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
