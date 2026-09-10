#!/usr/bin/env python3
"""repro_deadline.py — PUT /graph/vertices/batch (upsert of existing ids) with N concurrent writers while one store
node is frozen (SIGSTOP), plus a probe thread doing cheap GETs; prints per-request latency and outcome."""
import json, sys, threading, time, urllib.request, urllib.error
import os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "suite")); sys.path.insert(0, os.path.expanduser("~")); from hg_suite import HG
hg = HG("localhost", 8080, "DEFAULT", "hugegraph")
def put_batch(i, n=200):
    def props(j): return {"age": 18 + (j * 7) % 63, "score": round(((j * 37) % 1000) / 10.0, 1),
                          "ts": f"{2010 + (j * 13) % 15}-{1 + (j * 3) % 12:02d}-{1 + (j * 5) % 28:02d} 00:00:00", "cnt": 9}
    vs = [{"label": "person", "id": f"p{(i*n + k) % 3000:05d}", "properties": props((i*n + k) % 3000)} for k in range(n)]
    body = json.dumps({"vertices": vs, "update_strategies": {"cnt": "OVERRIDE"}}).encode()
    req = urllib.request.Request(hg.base + "/graph/vertices/batch", data=body, method="PUT", headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as r: st, msg = r.status, ""
    except urllib.error.HTTPError as e: st, msg = e.code, e.read()[:160].decode(errors="replace")
    except Exception as e: st, msg = 0, str(e)[:120]
    print(f"{time.strftime('%H:%M:%S')} PUT#{i} {time.time()-t0:7.1f}s -> {st} {msg}", flush=True)
def probe(stop):
    while not stop.is_set():
        for name, url in (("GET versions", hg.root + "/versions"), ("GET vertex p00010", hg.base + "/graph/vertices/%22p00010%22"), ("GET vertices person limit 1", hg.base + "/graph/vertices?label=person&limit=1")):
            t0 = time.time()
            try:
                with urllib.request.urlopen(urllib.request.Request(url), timeout=15) as r: st = r.status
            except urllib.error.HTTPError as e: st = e.code
            except Exception as e: st = f"ERR {type(e).__name__}"
            print(f"{time.strftime('%H:%M:%S')} probe {name:28} {time.time()-t0:6.2f}s -> {st}", flush=True)
        time.sleep(5)
W = int(sys.argv[1]) if len(sys.argv) > 1 else 6
stop = threading.Event(); pt = threading.Thread(target=probe, args=(stop,), daemon=True); pt.start()
ts = [threading.Thread(target=put_batch, args=(i,)) for i in range(W)]
[t.start() for t in ts]; [t.join() for t in ts]; stop.set(); print("writers done", flush=True)
