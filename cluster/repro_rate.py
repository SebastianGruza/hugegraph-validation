#!/usr/bin/env python3
"""repro_rate.py — F15 availability measurement under a steady write rate: one single-vertex POST every 1/RATE s
(each in its own thread, so slow requests do not throttle the writer) for WRITE_S seconds while one store node is
frozen, then no more writes; a probe GET (a normal API call, subject to LoadDetectFilter) runs every 2 s during the
writes and afterwards until the server answers 10 probes in a row or MAX_S pass. The store stays frozen throughout.
usage: repro_rate.py <rate_per_s> <write_seconds> <max_seconds> <tag>"""
import json, os, sys, threading, time, urllib.request, urllib.error, uuid
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "suite")); sys.path.insert(0, os.path.expanduser("~")); from hg_suite import HG
hg = HG("localhost", 8080, "DEFAULT", "hugegraph")
RATE, WS, MAXS, TAG = float(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
T0 = time.time(); lock = threading.Lock(); sent = 0; done = []; inflight = 0
def rel(): return f"{time.time()-T0:6.1f}s"
def post(i):
    global inflight
    req = urllib.request.Request(hg.base + "/graph/vertices", data=json.dumps({"label": "node", "id": f"{TAG}-{i}-{uuid.uuid4().hex[:6]}", "properties": {}}).encode(), method="POST", headers={"Content-Type": "application/json"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=3600) as r: st = str(r.status)
    except urllib.error.HTTPError as e: st = f"{e.code}"
    except Exception as e: st = f"ERR {type(e).__name__}"
    with lock: done.append((i, time.time() - t, st)); inflight -= 1
def writer():
    global sent, inflight
    while time.time() - T0 < WS:
        with lock: inflight += 1; i = sent; sent += 1
        threading.Thread(target=post, args=(i,), daemon=True).start()
        time.sleep(1.0 / RATE)
threading.Thread(target=writer, daemon=True).start()
def probe():
    t = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(hg.base + "/graph/vertices/%22p00010%22"), timeout=5) as r: st = str(r.status)
    except urllib.error.HTTPError as e: st = str(e.code)
    except Exception as e: st = "TIMEOUT" if "timed out" in str(e) else type(e).__name__
    return st, time.time() - t
streak = 0; dead_probes = 0; first_dead = None; last_dead = None; recovered_at = None; last_print = -10; dead_windows = []; cur = None
while time.time() - T0 < MAXS:
    st, dt = probe(); now = time.time() - T0; ok = st == "200"
    if ok:
        streak += 1
        if cur is not None: dead_windows.append((cur, now)); cur = None
    else:
        streak = 0; dead_probes += 1; last_dead = now; first_dead = first_dead if first_dead is not None else now
        if cur is None: cur = now
    with lock: infl, n_sent = inflight, sent
    if now - last_print >= 10 or (not ok and dead_probes == 1) or (ok and streak == 1 and dead_probes):
        print(f"{rel()} probe {'OK  ' if ok else 'DEAD'} vertex={st}/{dt:.1f}s  sent={n_sent} inflight={infl} {'(writing)' if now < WS else '(writes stopped)'}", flush=True); last_print = now
    if now > WS and streak >= 10: break
    time.sleep(2)
if cur is not None: dead_windows.append((cur, time.time() - T0))
with lock:
    sts = {}
    for _, _, s in done: sts[s] = sts.get(s, 0) + 1
    lat = sorted(d for _, d, _ in done)
tail = None if last_dead is None else round(max(0.0, last_dead - WS), 1)
print(f"SUMMARY {TAG}: rate={RATE}/s writes={WS}s sent={sent} finished={len(done)} still_inflight={inflight} statuses={sts} "
      f"post_latency_median={lat[len(lat)//2] if lat else None:.1f}s max={lat[-1] if lat else None:.1f}s "
      f"dead_probes={dead_probes} dead_windows={[(round(a,1), round(b,1)) for a,b in dead_windows]} first_dead={None if first_dead is None else round(first_dead,1)}s "
      f"rest_dead_after_writes_stopped={tail}s total={rel()}", flush=True)
