#!/usr/bin/env python3
"""summarize2.py <label>=<dir>/<variant> ... — one column per run: client result json, samplers (CPU/RSS/busy), disk split
(db / raft log / snapshot MB, RocksDB keys, per-table keys and MB from <variant>_disk_<host>.txt). Ratios vs the first column."""
import csv, glob, json, os, re, sys
cols = []
for arg in sys.argv[1:]:
    label, path = arg.split("=", 1); d, v = os.path.split(path)
    res = json.load(open(os.path.join(d, f"{v}.json")))
    r = {"edges/s": res["edges_per_s"], "load seconds": res["seconds"], "p50 ms": res["batch_ms"]["p50"], "p99 ms": res["batch_ms"]["p99"], "max ms": res["batch_ms"]["max"], "errors": sum(res["errors"].values())}
    s_cpu = v_cpu = s_rss = v_rss = 0; busy = 0.0; hosts = 0
    for f in sorted(glob.glob(os.path.join(d, f"{v}_*.csv"))):
        rs = list(csv.DictReader(open(f)))
        if len(rs) < 2: continue
        a, b = rs[0], rs[-1]; hosts += 1
        s_cpu += int(b["store_cpu_ticks"]) - int(a["store_cpu_ticks"]); v_cpu += int(b["server_cpu_ticks"]) - int(a["server_cpu_ticks"])
        s_rss += max(int(x["store_rss_kb"]) for x in rs) - int(a["store_rss_kb"]); v_rss = max(v_rss, max(int(x["server_rss_kb"]) for x in rs) - int(a["server_rss_kb"]))
        it, tt = int(b["sys_idle_ticks"]) - int(a["sys_idle_ticks"]), int(b["sys_total_ticks"]) - int(a["sys_total_ticks"]); busy += (1 - it / tt) * 100 if tt else 0
    r.update({"store CPU s (sum)": round(s_cpu / 100), "server CPU s": round(v_cpu / 100), "store RSS growth MB (sum)": round(s_rss / 1024), "avg node busy %": round(busy / max(hosts, 1))})
    db = log = snap = 0; tables = {}
    for f in sorted(glob.glob(os.path.join(d, f"{v}_disk_*.txt"))):
        t = open(f).read()
        m = re.search(r"db_MB=(\d+) raftlog_MB=(\d+) snapshot_MB=(\d+)", t)
        if m: db += int(m[1]); log += int(m[2]); snap += int(m[3])
        for tn, k, mb in re.findall(r"(g\+\w+):keys=(\d+),MB=(\d+)", t):
            tables.setdefault(tn, [0, 0]); tables[tn][0] += int(k); tables[tn][1] += int(mb)
    r.update({"data on disk MB (db, compacted)": db, "raft log MB": log, "raft snapshot MB": snap})
    for tn in ("g+oe", "g+ie", "g+index", "g+v"):
        if tn in tables: r[f"{tn} keys"] = tables[tn][0]; r[f"{tn} MB"] = tables[tn][1]
    cols.append((label, r))
keys = []
for _, r in cols:
    for k in r:
        if k not in keys: keys.append(k)
w = max(len(k) for k in keys); cw = max(12, max(len(l) for l, _ in cols) + 2)
print(f"{'':{w}} | " + " | ".join(f"{l:>{cw}}" for l, _ in cols) + " | " + " | ".join(f"{'x'+l[:cw-4]:>{cw}}" for l, _ in cols[1:]))
base = cols[0][1]
for k in keys:
    vals = [r.get(k, "") for _, r in cols]
    ratios = [f"{r.get(k)/base[k]:.2f}" if isinstance(base.get(k), (int, float)) and base.get(k) and isinstance(r.get(k), (int, float)) else "-" for _, r in cols[1:]]
    print(f"{k:{w}} | " + " | ".join(f"{str(x):>{cw}}" for x in vals) + " | " + " | ".join(f"{x:>{cw}}" for x in ratios))
