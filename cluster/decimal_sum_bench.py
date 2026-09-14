#!/usr/bin/env python3
"""decimal_sum_bench.py <port> [accounts=10000] [rounds=5] [threads=4] [batch=500]
HugeGraph DECIMAL + update_strategies check against an exact Python Decimal oracle.
Schema: account(name PK, balance DECIMAL, hi DECIMAL, lo DECIMAL), transfer edge (amount DECIMAL, sort key seq INT).
Rounds of PUT /graph/vertices/batch with SUM (balance), BIGGER (hi), SMALLER (lo); random increments up to
2^255 with 18-digit fractions, negative ones too. Then every account is read back and compared exactly."""
import gzip, json, random, sys, threading, time, urllib.parse, urllib.request, urllib.error
from decimal import Decimal, getcontext
getcontext().prec = 200
PORT = int(sys.argv[1]); N = int(sys.argv[2]) if len(sys.argv) > 2 else 10000
ROUNDS = int(sys.argv[3]) if len(sys.argv) > 3 else 5; T = int(sys.argv[4]) if len(sys.argv) > 4 else 4
B = int(sys.argv[5]) if len(sys.argv) > 5 else 500
BASE = f"http://127.0.0.1:{PORT}/graphspaces/DEFAULT/graphs/hugegraph"; VERIFY_ONLY = len(sys.argv) > 6 and sys.argv[6] == "verify"
def call(method, path, body=None, timeout=600):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(), method=method,
                                 headers={"Content-Type": "application/json", "Accept-Encoding": "identity"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: b = r.read(); return r.status, (gzip.decompress(b) if b[:2] == b"\x1f\x8b" else b)
    except urllib.error.HTTPError as e: b = e.read(); return e.code, (gzip.decompress(b) if b[:2] == b"\x1f\x8b" else b)
def must(st, body, ok=(200, 201, 202)):
    if st not in ok: raise SystemExit(f"HTTP {st}: {body[:300]}")
    return json.loads(body) if body else None
# schema
for name in (() if VERIFY_ONLY else ("balance", "hi", "lo", "amount")):
    must(*call("POST", "/schema/propertykeys", {"name": name, "data_type": "DECIMAL", "cardinality": "SINGLE"}))
if not VERIFY_ONLY: must(*call("POST", "/schema/propertykeys", {"name": "name", "data_type": "TEXT", "cardinality": "SINGLE"}))
if not VERIFY_ONLY: must(*call("POST", "/schema/propertykeys", {"name": "seq", "data_type": "INT", "cardinality": "SINGLE"}))
if not VERIFY_ONLY: must(*call("POST", "/schema/vertexlabels", {"name": "account", "id_strategy": "PRIMARY_KEY", "primary_keys": ["name"],
                                            "properties": ["name", "balance", "hi", "lo"], "nullable_keys": ["balance", "hi", "lo"]}))
if not VERIFY_ONLY: must(*call("POST", "/schema/edgelabels", {"name": "transfer", "source_label": "account", "target_label": "account", "frequency": "MULTIPLE",
                                          "sort_keys": ["seq"], "properties": ["seq", "amount"], "nullable_keys": ["amount"]}))
if VERIFY_ONLY: st, body = 400, b'{"message":"skipped"}'
else: st, body = call("POST", "/schema/edgelabels", {"name": "bad", "source_label": "account", "target_label": "account", "frequency": "MULTIPLE",
                                                "sort_keys": ["amount"], "properties": ["amount"]})
print("decimal sort key rejected:", st, json.loads(body).get("message", "")[:80])
if VERIFY_ONLY: st, body = 400, b'{"message":"skipped"}'
else: st, body = call("POST", "/schema/indexlabels", {"name": "byBalance", "base_type": "VERTEX_LABEL", "base_value": "account", "index_type": "RANGE", "fields": ["balance"]})
print("decimal range index rejected:", st, json.loads(body).get("message", "")[:80])
# oracle
rnd = random.Random(7); names = [f"acct{i:06d}" for i in range(N)]
oracle = {n: {"balance": Decimal(0), "hi": None, "lo": None} for n in names}
def rand_dec(r):
    mag = r.choice([r.getrandbits(8), r.getrandbits(64), r.getrandbits(128), r.getrandbits(255)])
    frac = r.getrandbits(60) % (10 ** 18)
    d = Decimal(mag) + Decimal(frac) / Decimal(10 ** 18)
    return -d if r.random() < 0.3 else d
def dstr(d): return format(d, "f")
# initial create with SUM semantics (no existing vertex -> plain create)
t0 = time.time(); lat = []; errors = {}; lock = threading.Lock(); dupset = set(); SAME_CHUNK = "samechunk" in sys.argv
def upsert(chunk, strategies):
    vs = [{"label": "account", "properties": {"name": n, "balance": dstr(b), "hi": dstr(b), "lo": dstr(b)}} for n, b in chunk]
    t = time.time(); st, body = call("PUT", "/graph/vertices/batch", {"vertices": vs, "update_strategies": strategies, "create_if_not_exist": True})
    with lock:
        lat.append(time.time() - t)
        if st != 200: errors[f"{st} {body[:80]}"] = errors.get(f"{st} {body[:80]}", 0) + 1
for rd in range(ROUNDS):
    r = random.Random(100 + rd); work = []
    for n in names:
        d = rand_dec(r); o = oracle[n]
        o["balance"] += d
        o["hi"] = d if o["hi"] is None else max(o["hi"], d)
        o["lo"] = d if o["lo"] is None else min(o["lo"], d)
        work.append((n, d))
    # duplicate a few accounts inside one batch to exercise the in-request combine path
    dup = r.sample(names, 50); dupset.update(dup); extra = []
    for n in dup:
        d = rand_dec(r); o = oracle[n]; o["balance"] += d; o["hi"] = max(o["hi"], d); o["lo"] = min(o["lo"], d); extra.append((n, d))
    r.shuffle(work)
    if SAME_CHUNK:
        # the duplicate goes right after its first entry: same batch, combined in-request
        pos = {n: i for i, (n, _) in enumerate(work)}
        for n, d in sorted(extra, key=lambda x: -pos[x[0]]): work.insert(pos[n] + 1, (n, d))
    else:
        work.extend(extra); r.shuffle(work)
    chunks = [work[i:i + B] for i in range(0, len(work), B)]
    idx = [0]
    def worker():
        while True:
            with lock:
                if idx[0] >= len(chunks): return
                c = chunks[idx[0]]; idx[0] += 1
            upsert(c, {"balance": "SUM", "hi": "BIGGER", "lo": "SMALLER"})
    ts = [threading.Thread(target=worker) for _ in range(T)] if not VERIFY_ONLY else []; [x.start() for x in ts]; [x.join() for x in ts]
    print(f"round {rd}: {len(work)} upserts, {time.time() - t0:.1f}s so far, errors={errors}", flush=True)
# edges with decimal amount, read back through sort-key traversal
edges = [{"label": "transfer", "outV": f"1:{names[i]}", "outVLabel": "account", "inV": f"1:{names[(i + 1) % N]}", "inVLabel": "account",
          "properties": {"seq": i % 7, "amount": dstr(rand_dec(rnd))}} for i in range(2000)]
if not VERIFY_ONLY: must(*call("POST", "/graph/edges/batch?check_vertex=false", edges))
# verify
lat.sort() if lat else lat.append(0.0); mism = 0; mism_dup = 0; checked = 0; t1 = time.time()
for i in range(0, N, 1000):
    st, body = call("GET", f"/graph/vertices?label=account&limit=1000&page=&properties=" + urllib.parse.quote(json.dumps({"name": f"P.gte(\"{names[i]}\")"})) if False else "/graph/vertices?label=account&limit=100000")
    break
st, body = call("GET", "/graph/vertices?label=account&limit=100000"); data = must(st, body)["vertices"]
for v in data:
    p = v["properties"]; n = p["name"]; o = oracle[n]; checked += 1
    got = (Decimal(p["balance"]), Decimal(p["hi"]), Decimal(p["lo"]))
    exp = (o["balance"], o["hi"], o["lo"])
    if any(g != e for g, e in zip(got, exp)):
        mism += 1; mism_dup += n in dupset
        if mism <= 2: print("MISMATCH", n, "in-dup-set" if n in dupset else "NOT-dup", "got", [dstr(x)[:40] for x in got], "exp", [dstr(x)[:40] for x in exp])
    if not isinstance(p["balance"], str): print("NOT A STRING:", n, p["balance"]); mism += 1
print(f"verified {checked}/{N} accounts, mismatches={mism} (of which in the duplicated set: {mism_dup}), verify {time.time() - t1:.1f}s")
st, body = call("GET", f"/graph/edges?vertex_id=%221:{names[0]}%22&direction=OUT&label=transfer&limit=10")
e = must(st, body)["edges"]; print("edge amount via sort-key traversal:", e[0]["properties"]["amount"] if e else "NONE", "expected", edges[0]["properties"]["amount"])
p = lambda q: lat[min(len(lat) - 1, int(q * len(lat)))] * 1000
print(json.dumps({"accounts": N, "rounds": ROUNDS, "batches": len(lat), "batch_ms": {"p50": round(p(.5)), "p99": round(p(.99)), "max": round(lat[-1] * 1000)}, "errors": errors, "mismatches": mism}))
