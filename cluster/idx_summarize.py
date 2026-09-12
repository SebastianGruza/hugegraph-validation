#!/usr/bin/env python3
"""summarize.py <dir> — reads <variant>_<host>.csv samplers and <variant>.json results, prints a comparison table."""
import csv, glob, json, os, sys
d = sys.argv[1]; rows = {}
for v in ("noindex", "index"):
    res = json.load(open(os.path.join(d, f"{v}.json")))
    r = {"edges/s": res["edges_per_s"], "seconds": res["seconds"], "p50 ms": res["batch_ms"]["p50"], "p99 ms": res["batch_ms"]["p99"], "max ms": res["batch_ms"]["max"], "errors": sum(res["errors"].values())}
    disk = 0; s_cpu = 0; v_cpu = 0; s_rss_peak = 0; v_rss_peak = 0; sys_busy = 0.0; hosts = 0
    for f in sorted(glob.glob(os.path.join(d, f"{v}_*.csv"))):
        rs = list(csv.DictReader(open(f)))
        if len(rs) < 2: continue
        a, b = rs[0], rs[-1]; hosts += 1
        disk += int(b["storage_kb"]) - int(a["storage_kb"]); s_cpu += int(b["store_cpu_ticks"]) - int(a["store_cpu_ticks"]); v_cpu += int(b["server_cpu_ticks"]) - int(a["server_cpu_ticks"])
        s_rss_peak += max(int(x["store_rss_kb"]) for x in rs) - int(a["store_rss_kb"]); v_rss_peak = max(v_rss_peak, max(int(x["server_rss_kb"]) for x in rs) - int(a["server_rss_kb"]))
        it, tt = int(b["sys_idle_ticks"]) - int(a["sys_idle_ticks"]), int(b["sys_total_ticks"]) - int(a["sys_total_ticks"]); sys_busy += (1 - it / tt) * 100 if tt else 0
    r.update({"disk MB (3 stores)": round(disk / 1024), "store CPU s (sum)": round(s_cpu / 100), "server CPU s": round(v_cpu / 100), "store RSS growth MB (sum)": round(s_rss_peak / 1024), "server RSS growth MB": round(v_rss_peak / 1024), "avg node busy %": round(sys_busy / max(hosts, 1))})
    rows[v] = r
keys = list(rows["noindex"].keys()); w = max(len(k) for k in keys)
print(f"{'':{w}} | {'no index':>12} | {'label+addr index':>16} | {'ratio':>6}")
for k in keys:
    a, b = rows["noindex"][k], rows["index"][k]; ratio = f"{b/a:.2f}" if isinstance(a, (int, float)) and a else "-"
    print(f"{k:{w}} | {a:>12} | {b:>16} | {ratio:>6}")
