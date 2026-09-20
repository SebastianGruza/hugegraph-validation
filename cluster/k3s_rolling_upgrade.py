#!/usr/bin/env python3
"""Rolling upgrade under load on the cluster preset (3 PD + 3 Store + 3 Server), one component at a time
in the order of the operations guide (Store -> PD -> Server), driven the way a chart upgrade drives it
(`helm upgrade` with a changed pod annotation / image tag, no --wait, then `rollout status` per component).

Sampled at 1 Hz throughout: per-pod Ready, Server Service endpoints, PD leader, partition leaders per Store,
Server /readiness; the fault-battery writer + reader run through the Service; the oracle is verified after.

  python3 k3s_rolling_upgrade.py <server-image-tag-after>
"""
import json, os, sys, threading, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import k3s_faults as F  # noqa: E402

OUT = os.path.expanduser("~/validation/k3s-rolling-upgrade")
F.OUT = os.path.expanduser("~/validation/k3s-faults")
CHART = os.path.expanduser("~/hg-chart/helm/hugegraph")
TAG_AFTER = sys.argv[1]


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(os.path.join(OUT, "rolling-upgrade.log"), "a") as f:
        f.write(line + "\n")


def http(url, timeout=4):
    req = urllib.request.Request(url, headers={"Connection": "close", "Accept-Encoding": "identity"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def endpoints():
    out = F.kc("get endpoints hugegraph-server -o jsonpath='{.subsets[*].addresses[*].ip}'", check=False)
    return len([x for x in out.replace("'", "").split() if x])


class Sampler(threading.Thread):

    def __init__(self):
        super().__init__(daemon=True)
        self.rows, self.stop, self.t0, self.marks = [], threading.Event(), time.time(), []

    def mark(self, what):
        self.marks.append((round(time.time() - self.t0, 1), what)); log(what)

    def run(self):
        while not self.stop.is_set():
            t = round(time.time() - self.t0, 1)
            pods = F.pods()
            row = {"t": t, "endpoints": endpoints(),
                   "ready": {n: p["ready"] for n, p in pods.items()},
                   "pd_leader": F.pd_leader()[0], "part_leaders": F.partition_leaders()}
            row["srv_readiness"] = {n: http(f"http://{p['ip']}:8080/readiness") for n, p in pods.items()
                                    if "server" in n and p["ip"]}
            self.rows.append(row)
            time.sleep(max(0.0, 1.0 - ((time.time() - self.t0) % 1.0)))

    def finish(self):
        self.stop.set(); self.join(timeout=15)
        with open(os.path.join(OUT, "samples.json"), "w") as f:
            json.dump({"marks": self.marks, "rows": self.rows}, f)
        return self.rows


def helm(args, timeout=900):
    return F.sh(f"helm upgrade hugegraph {CHART} -n hugegraph --reuse-values {args}", check=True, timeout=timeout)


def rollout(kind, name, timeout=900):
    t0 = time.time()
    F.kc(f"rollout status {kind}/{name} --timeout={timeout}s", timeout=timeout + 30)
    return round(time.time() - t0, 1)


def main():
    os.makedirs(OUT, exist_ok=True)
    rest = F.Rest()
    oracle = json.load(open(os.path.join(F.OUT, "oracle.json"))); oracle.setdefault("acked", {})
    stamp = time.strftime("%Y%m%dT%H%M%S")
    s = Sampler(); s.start(); time.sleep(15)
    ld = F.Load(rest, oracle, "upg"); ld.t_start = time.time(); ld.start(); time.sleep(20)
    result = {"tag_after": TAG_AFTER, "steps": {}}
    for kind, name, args in [("statefulset", "hugegraph-store", f"--set store.podAnnotations.upgrade={stamp}"),
                             ("statefulset", "hugegraph-pd", f"--set pd.podAnnotations.upgrade={stamp}"),
                             ("deployment", "hugegraph-server", f"--set-string server.image.tag={TAG_AFTER} --set server.podAnnotations.upgrade={stamp}")]:
        s.mark(f"upgrade {name}: helm upgrade {args}")
        t_step = time.time()
        helm(args)
        took = rollout(kind, name)
        s.mark(f"{name} rolled out in {took}s")
        result["steps"][name] = {"rollout_s": took, "load": ld.summary(t_step, time.time())}
        time.sleep(30)
    time.sleep(30)
    ld.stop.set(); ld.join(timeout=150)
    oracle["acked"].update(ld.acked); json.dump(oracle, open(os.path.join(F.OUT, "oracle.json"), "w"))
    rows = s.finish()
    bad, lost = F.verify(rest, oracle, sample=400, tag="rolling-upgrade/after")
    min_eps = min(r["endpoints"] for r in rows)
    leaderless = sum(1 for r in rows if not r["pd_leader"])
    result.update({"load_total": ld.summary(ld.t_start, time.time()), "min_server_endpoints": min_eps,
                   "pd_leaderless_samples": leaderless, "verify_mismatches": len(bad), "acked_lost": len(lost),
                   "images_after": F.kc("get pods -o jsonpath='{range .items[*]}{.metadata.name}={.status.containerStatuses[0].imageID}{\"\\n\"}{end}'", check=False).replace("'", "")})
    with open(os.path.join(OUT, "rolling-upgrade.json"), "w") as f:
        json.dump(result, f, indent=1, default=str)
    log("RESULT " + json.dumps({k: v for k, v in result.items() if k != "images_after"}, default=str)[:1500])


if __name__ == "__main__":
    main()
