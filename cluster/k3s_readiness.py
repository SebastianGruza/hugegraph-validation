#!/usr/bin/env python3
"""Storage-aware Server readiness (apache/hugegraph#3212) on the Helm chart, on pods.

Run on a k3s node with kubectl access. Reuses the oracle/load harness of k3s_faults.py
(same directory). Every scenario samples, once per second, on every Server pod:
  GET /readiness (200/503/401), GET /versions, the pod Ready condition and the Service endpoints,
and reports the transition times. The load runs through the Service, so a Server that stays
in the Service without storage shows up as failed requests.

  python3 k3s_readiness.py setup                       schema + oracle (from k3s_faults)
  python3 k3s_readiness.py baseline                    60 s of sampling, latency and cache ratio
  python3 k3s_readiness.py stores-zero                 Stores -> 0, then back to 3
  python3 k3s_readiness.py store-one-down              one Store pod force-deleted
  python3 k3s_readiness.py store-roll                  rolling restart of the Store StatefulSet under load
  python3 k3s_readiness.py pd-zero                     PD -> 0, then back
  python3 k3s_readiness.py pd-roll                     rolling restart of PD under load
  python3 k3s_readiness.py store-freeze                SIGSTOP the Store leading most partitions, 60 s
"""
import json, os, subprocess, sys, threading, time, urllib.request, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import k3s_faults as F  # noqa: E402

OUT = os.path.expanduser("~/validation/k3s-readiness")
F.OUT = os.path.expanduser("~/validation/k3s-faults")   # oracle lives with the fault battery
NS = "hugegraph"


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(os.path.join(OUT, "readiness.log"), "a") as f:
        f.write(line + "\n")


def http(url, timeout=4):
    req = urllib.request.Request(url, headers={"Connection": "close", "Accept-Encoding": "identity"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace"), time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace"), time.time() - t0
    except Exception as e:
        return 0, str(e)[:120], time.time() - t0


def servers():
    return F.pods("-l app.kubernetes.io/component=server")


def endpoints():
    out = F.kc("get endpoints hugegraph-server -o jsonpath='{.subsets[*].addresses[*].ip}'", check=False)
    return sorted(x for x in out.replace("'", "").split() if x)


class Sampler(threading.Thread):
    """1 Hz samples of /readiness, /versions, Ready condition and Service endpoints per Server pod."""

    def __init__(self, tag):
        super().__init__(daemon=True)
        self.tag, self.rows, self.stop = tag, [], threading.Event()
        self.t0 = time.time()

    def run(self):
        while not self.stop.is_set():
            t = round(time.time() - self.t0, 1)
            eps = endpoints()
            for name, p in servers().items():
                if not p["ip"]:
                    self.rows.append({"t": t, "pod": name, "ip": None, "ready_cond": p["ready"]})
                    continue
                st, body, dt = http(f"http://{p['ip']}:8080/readiness")
                vs, _, _ = http(f"http://{p['ip']}:8080/versions")
                row = {"t": t, "pod": name, "ip": p["ip"], "readiness": st, "ms": round(dt * 1000),
                       "versions": vs, "ready_cond": p["ready"], "in_service": p["ip"] in eps,
                       "endpoints": len(eps)}
                try:
                    j = json.loads(body)
                    row.update({"reason": j.get("reason"), "cached": j.get("cached"),
                                "active_stores": j.get("active_stores"),
                                "answered_store": j.get("answered_store"),
                                "pd_reachable": j.get("pd_reachable")})
                except Exception:
                    row["reason"] = body[:80]
                self.rows.append(row)
            time.sleep(max(0.0, 1.0 - ((time.time() - self.t0) % 1.0)))

    def finish(self):
        self.stop.set()
        self.join(timeout=10)
        with open(os.path.join(OUT, f"{self.tag}.samples.json"), "w") as f:
            json.dump(self.rows, f)
        return self.rows


def transitions(rows, key):
    """[(t, pod, old, new)] for changes of `key` per pod."""
    last, out = {}, []
    for r in rows:
        v = r.get(key)
        if r["pod"] in last and last[r["pod"]] != v:
            out.append((r["t"], r["pod"], last[r["pod"]], v))
        last[r["pod"]] = v
    return out


def first_time(rows, pred, after=0.0):
    for r in rows:
        if r["t"] >= after and pred(r):
            return r["t"]
    return None


def summarize(rows):
    codes = {}
    for r in rows:
        codes[r.get("readiness")] = codes.get(r.get("readiness"), 0) + 1
    lat = sorted(r["ms"] for r in rows if r.get("readiness") in (200, 503))
    p50 = lat[len(lat) // 2] if lat else None
    p99 = lat[int(len(lat) * 0.99)] if lat else None
    cached = sum(1 for r in rows if r.get("cached") is True)
    return {"samples": len(rows), "codes": codes, "p50_ms": p50, "p99_ms": p99, "max_ms": max(lat) if lat else None,
            "cached_ratio": round(cached / len(rows), 2) if rows else None,
            "readiness_transitions": transitions(rows, "readiness"),
            "ready_cond_transitions": transitions(rows, "ready_cond"),
            "in_service_transitions": transitions(rows, "in_service")}


def result(name, verdict, evidence, extra=None):
    r = {"scenario": name, "verdict": verdict, "evidence": evidence, "extra": extra,
         "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(os.path.join(OUT, f"{name}.json"), "w") as f:
        json.dump(r, f, indent=1, default=str)
    log(f"RESULT {name}: {verdict} {evidence}")
    return r


def wait_pods(selector, want, timeout=600):
    return F.wait_ready(selector, want, timeout)


def start_load(rest, oracle, tag):
    ld = F.Load(rest, oracle, tag); ld.t_start = time.time(); ld.start()
    return ld


def stop_load(ld, oracle):
    ld.stop.set(); ld.join(timeout=150)
    oracle["acked"].update(ld.acked)
    json.dump(oracle, open(os.path.join(F.OUT, "oracle.json"), "w"))
    return ld.summary(ld.t_start, time.time())


# ----------------------------------------------------------------------------- scenarios
def baseline(rest, oracle):
    s = Sampler("baseline"); s.start(); time.sleep(60); rows = s.finish()
    sm = summarize(rows)
    ok = sm["codes"].get(200, 0) == sm["samples"] and not sm["readiness_transitions"]
    return result("baseline", "PASS" if ok else "FAIL",
                  f"{sm['samples']} samples, codes {sm['codes']}, p50 {sm['p50_ms']} ms, p99 {sm['p99_ms']} ms, "
                  f"cached ratio {sm['cached_ratio']}", sm)


def stores_zero(rest, oracle):
    s = Sampler("stores-zero"); s.start(); time.sleep(10)
    ld = start_load(rest, oracle, "stores-zero"); time.sleep(10)
    t_fault = round(time.time() - s.t0, 1)
    F.kc("scale statefulset hugegraph-store --replicas=0"); log(f"stores -> 0 at t={t_fault}")
    time.sleep(120)
    t_back = round(time.time() - s.t0, 1)
    F.kc("scale statefulset hugegraph-store --replicas=3"); log(f"stores -> 3 at t={t_back}")
    up = wait_pods("-l app.kubernetes.io/component=store", 3)
    time.sleep(45)
    lsum = stop_load(ld, oracle)
    rows = s.finish(); sm = summarize(rows)
    per_pod = {}
    for name in servers():
        rs = [r for r in rows if r["pod"] == name]
        per_pod[name] = {
            "readiness_503_after_fault_s": None if (x := first_time(rs, lambda r: r.get("readiness") == 503, t_fault)) is None else round(x - t_fault, 1),
            "not_ready_after_fault_s": None if (x := first_time(rs, lambda r: r.get("ready_cond") is False, t_fault)) is None else round(x - t_fault, 1),
            "out_of_service_after_fault_s": None if (x := first_time(rs, lambda r: r.get("in_service") is False, t_fault)) is None else round(x - t_fault, 1),
            "readiness_200_after_back_s": None if (x := first_time(rs, lambda r: r.get("readiness") == 200, t_back)) is None else round(x - t_back, 1),
            "ready_after_back_s": None if (x := first_time(rs, lambda r: r.get("ready_cond") is True, t_back)) is None else round(x - t_back, 1),
            "reasons_during_fault": sorted({r.get("reason") for r in rs if t_fault <= r["t"] < t_back and r.get("readiness") == 503})[:3],
        }
    min_eps = min((r["endpoints"] for r in rows if t_fault + 40 <= r["t"] < t_back), default=None)
    ok = all(v["readiness_503_after_fault_s"] is not None and v["not_ready_after_fault_s"] is not None
             and v["readiness_200_after_back_s"] is not None and v["ready_after_back_s"] is not None
             for v in per_pod.values()) and min_eps == 0
    return result("stores-zero", "PASS" if ok else "FAIL",
                  f"per pod {json.dumps(per_pod)}; min endpoints during fault {min_eps}; stores back Ready in {up}s; "
                  f"load {lsum}", {"summary": sm, "per_pod": per_pod, "t_fault": t_fault, "t_back": t_back, "load": lsum})


def store_one_down(rest, oracle):
    s = Sampler("store-one-down"); s.start(); time.sleep(10)
    ld = start_load(rest, oracle, "store-one-down"); time.sleep(10)
    t_fault = round(time.time() - s.t0, 1)
    F.kc("delete pod hugegraph-store-1 --force --grace-period=0"); log(f"store-1 force-deleted at t={t_fault}")
    up = wait_pods("-l app.kubernetes.io/component=store", 3); time.sleep(30)
    lsum = stop_load(ld, oracle); rows = s.finish(); sm = summarize(rows)
    flaps = [x for x in sm["readiness_transitions"]] + sm["ready_cond_transitions"]
    ok = not flaps and sm["codes"].get(200, 0) == sm["samples"]
    return result("store-one-down", "PASS" if ok else "FAIL",
                  f"codes {sm['codes']}, transitions {flaps}, store back Ready in {up}s, load {lsum}", sm)


def store_roll(rest, oracle):
    s = Sampler("store-roll"); s.start(); time.sleep(10)
    ld = start_load(rest, oracle, "store-roll"); time.sleep(10)
    t_fault = round(time.time() - s.t0, 1)
    F.kc("rollout restart statefulset hugegraph-store"); log(f"store rollout restart at t={t_fault}")
    F.kc("rollout status statefulset hugegraph-store --timeout=600s", timeout=620); time.sleep(30)
    lsum = stop_load(ld, oracle); rows = s.finish(); sm = summarize(rows)
    flaps = sm["readiness_transitions"] + sm["ready_cond_transitions"] + sm["in_service_transitions"]
    ok = not flaps and sm["codes"].get(200, 0) == sm["samples"]
    return result("store-roll", "PASS" if ok else "FAIL",
                  f"codes {sm['codes']}, transitions {flaps}, answered stores {sorted({str(r.get('answered_store')) for r in rows})}, "
                  f"load {lsum}", sm)


def pd_zero(rest, oracle):
    """PD alone being down must not change readiness (the data plane serves without PD);
    the body has to say pd_reachable=false meanwhile and true again once PD is back."""
    s = Sampler("pd-zero"); s.start(); time.sleep(10)
    ld = start_load(rest, oracle, "pd-zero"); time.sleep(10)
    t_fault = round(time.time() - s.t0, 1)
    F.kc("scale statefulset hugegraph-pd --replicas=0"); log(f"pd -> 0 at t={t_fault}")
    time.sleep(90)
    t_back = round(time.time() - s.t0, 1)
    F.kc("scale statefulset hugegraph-pd --replicas=3"); log(f"pd -> 3 at t={t_back}")
    up = wait_pods("-l app.kubernetes.io/component=pd", 3); time.sleep(60)
    lsum = stop_load(ld, oracle)
    rows = s.finish(); sm = summarize(rows)
    per_pod = {}
    for name in servers():
        rs = [r for r in rows if r["pod"] == name]
        during = [r for r in rs if t_fault + 5 <= r["t"] < t_back]
        per_pod[name] = {
            "codes_during_fault": sorted({r.get("readiness") for r in during}),
            "pd_unreachable_seen_after_s": None if (x := first_time(rs, lambda r: r.get("pd_reachable") is False, t_fault)) is None else round(x - t_fault, 1),
            "pd_reachable_again_after_s": None if (x := first_time(rs, lambda r: r.get("pd_reachable") is True, t_back)) is None else round(x - t_back, 1),
            "ready_cond_transitions": [t for t in sm["ready_cond_transitions"] if t[1] == name],
        }
    ok = all(v["codes_during_fault"] == [200] and v["pd_unreachable_seen_after_s"] is not None
             and v["pd_reachable_again_after_s"] is not None and not v["ready_cond_transitions"]
             for v in per_pod.values())
    return result("pd-zero", "PASS" if ok else "FAIL",
                  f"per pod {json.dumps(per_pod)}; pd back Ready in {up}s; load {lsum}",
                  {"summary": sm, "per_pod": per_pod, "t_fault": t_fault, "t_back": t_back, "load": lsum})


def pd_roll(rest, oracle):
    s = Sampler("pd-roll"); s.start(); time.sleep(10)
    ld = start_load(rest, oracle, "pd-roll"); time.sleep(10)
    t_fault = round(time.time() - s.t0, 1)
    F.kc("rollout restart statefulset hugegraph-pd"); log(f"pd rollout restart at t={t_fault}")
    F.kc("rollout status statefulset hugegraph-pd --timeout=600s", timeout=620); time.sleep(30)
    lsum = stop_load(ld, oracle); rows = s.finish(); sm = summarize(rows)
    flaps = sm["readiness_transitions"] + sm["ready_cond_transitions"]
    return result("pd-roll", "PASS" if not sm["ready_cond_transitions"] else "FAIL",
                  f"codes {sm['codes']}, readiness transitions {sm['readiness_transitions']}, "
                  f"ready-condition transitions {sm['ready_cond_transitions']}, load {lsum}", sm)


def store_freeze(rest, oracle):
    victim, leaders = F.busiest_store()
    s = Sampler("store-freeze"); s.start(); time.sleep(10)
    ld = start_load(rest, oracle, "store-freeze"); time.sleep(10)
    t_fault = round(time.time() - s.t0, 1)
    pid = F.signal_java(victim, "store", "STOP"); log(f"SIGSTOP {victim} (leads {leaders}) pid {pid} at t={t_fault}")
    time.sleep(60)
    try:
        F.signal_java(victim, "store", "CONT"); log("SIGCONT")
    except Exception as e:
        log(f"SIGCONT skipped, the container was restarted by the liveness probe meanwhile: {str(e)[:80]}")
    time.sleep(30)
    lsum = stop_load(ld, oracle); rows = s.finish(); sm = summarize(rows)
    ok = not sm["ready_cond_transitions"]
    return result("store-freeze", "PASS" if ok else "FAIL",
                  f"victim {victim} leading {leaders}; codes {sm['codes']}; readiness transitions {sm['readiness_transitions']}; "
                  f"answered stores {sorted({str(r.get('answered_store')) for r in rows})}; load {lsum}", sm)


SCENARIOS = {"baseline": baseline, "stores-zero": stores_zero, "store-one-down": store_one_down,
             "store-roll": store_roll, "pd-zero": pd_zero, "pd-roll": pd_roll, "store-freeze": store_freeze}


def main():
    os.makedirs(OUT, exist_ok=True); os.makedirs(F.OUT, exist_ok=True)
    what = sys.argv[1]
    rest = F.Rest()
    if what == "setup":
        F.setup(rest); return
    oracle = json.load(open(os.path.join(F.OUT, "oracle.json")))
    oracle.setdefault("acked", {})
    for name in (list(SCENARIOS) if what == "all" else what.split(",")):
        log(f"=== {name}")
        SCENARIOS[name](rest, oracle)
        F.wait_ready("-l app.kubernetes.io/component=server", 3)
        time.sleep(10)


if __name__ == "__main__":
    main()
