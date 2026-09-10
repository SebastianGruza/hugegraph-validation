#!/usr/bin/env python3
"""repro_rest_dead.py — F15 reproduction: N writer threads loop single-vertex POST /graph/vertices (not subject to the
batch-write cap) while one store node is frozen; a probe polls cheap GETs every 2 s for the whole run and after the
store is thawed, until the server has answered 5 probes in a row. Prints a timeline and a summary.
usage: repro_rest_dead.py <writers> <write_seconds> <tag>   (freezing/thawing the store is done by the driver script)"""
import json, os, sys, threading, time, urllib.request, urllib.error, uuid
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "suite")); sys.path.insert(0, os.path.expanduser("~")); from hg_suite import HG
hg = HG("localhost", 8080, "DEFAULT", "hugegraph")
W, DUR, TAG = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
T0 = time.time(); lock = threading.Lock(); stats = {"ok": 0, "err": {}, "inflight": 0, "max_inflight": 0}
def rel(): return f"{time.time()-T0:6.1f}s"
def writer(i):
    n = 0
    while time.time() - T0 < DUR:
        vid = f"{TAG}-{i}-{n}-{uuid.uuid4().hex[:6]}"; n += 1
        body = json.dumps({"label": "node", "id": vid, "properties": {}}).encode()
        req = urllib.request.Request(hg.base + "/graph/vertices", data=body, method="POST", headers={"Content-Type": "application/json"})
        with lock: stats["inflight"] += 1; stats["max_inflight"] = max(stats["max_inflight"], stats["inflight"])
        t = time.time()
        try:
            with urllib.request.urlopen(req, timeout=1800) as r: st = r.status
        except urllib.error.HTTPError as e: st = f"{e.code} {e.read()[:70].decode(errors='replace')}"
        except Exception as e: st = f"ERR {type(e).__name__}"
        dt = time.time() - t
        with lock:
            stats["inflight"] -= 1
            if st == 201 or st == 200: stats["ok"] += 1
            else: stats["err"][str(st)[:60]] = stats["err"].get(str(st)[:60], 0) + 1
        if dt > 2 or (st != 201 and st != 200): print(f"{rel()} writer#{i} POST {dt:6.1f}s -> {st}", flush=True)
def probe_once():
    res = []
    for name, url in (("versions", hg.root + "/versions"), ("vertex", hg.base + "/graph/vertices/%22p00010%22")):
        t = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(url), timeout=5) as r: st = str(r.status)
        except urllib.error.HTTPError as e: st = str(e.code)
        except Exception as e: st = "TIMEOUT" if "timed out" in str(e) else type(e).__name__
        res.append((name, st, time.time() - t))
    return res
ts = [threading.Thread(target=writer, args=(i,), daemon=True) for i in range(W)]
[t.start() for t in ts]
timeline = []; ok_streak = 0; first_fail = None; last_fail = None; dead = 0
while True:
    r = probe_once(); alive = all(st == "200" for _, st, _ in r); now = time.time() - T0
    if not alive:
        dead += 1; ok_streak = 0; first_fail = first_fail or now; last_fail = now
    else: ok_streak += 1
    with lock: infl = stats["inflight"]
    print(f"{rel()} probe {'OK  ' if alive else 'DEAD'} " + " ".join(f"{n}={st}/{dt:.1f}s" for n, st, dt in r) + f"  inflight={infl}", flush=True)
    if now > DUR and (ok_streak >= 5 or now > DUR + 1500): break
    time.sleep(2)
with lock: print(f"SUMMARY {TAG}: writers={W} write_window={DUR}s posts_ok={stats['ok']} errors={stats['err']} max_inflight={stats['max_inflight']} "
                 f"probe_dead_count={dead} first_dead={first_fail and round(first_fail,1)}s last_dead={last_fail and round(last_fail,1)}s total={rel()}", flush=True)
