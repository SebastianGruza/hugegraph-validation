#!/usr/bin/env python3
"""k3s_faults.py <scenario> [--tag T] [--ns hugegraph] [--out DIR]
Fault battery for the HugeGraph Helm chart (apache/hugegraph#3132) on a live Kubernetes cluster, run from a node
that has kubectl access and can reach the Pod/Service network. Every scenario writes its oracle before the fault
(pod uids, restart counts, PD leader, acknowledged writes), proves the fault landed (new uid / restart count /
frozen process / PD leader change), keeps a writer and a reader running through the fault, and judges only against
what it recorded. Verdicts: PASS / FAIL / INCONCLUSIVE with a blame class (chart | image | harness | environment).

scenarios:
  setup         schema + base data (vertices with edges), oracle saved to <out>/oracle.json
  verify        every vertex and edge of the oracle readable through the Server Service, exact
  store-crash   kubectl delete --force one Store pod under load
  store-stall   SIGSTOP the Store JVM for STALL_S seconds under load (F15/F16 shape, #3204 on the image)
  pd-restart    delete the PD leader pod under load; then schema created via server A must be visible via B and C
  pd-freeze     SIGSTOP the PD leader JVM for FREEZE_S seconds: time without a PD leader, Server impact
  cold-start    Stores scaled to 0, Servers rolled: no Server exit within the startup budget; Stores back -> Ready
"""
import argparse, base64, json, os, random, subprocess, sys, threading, time, urllib.request, urllib.error, gzip

NS = "hugegraph"
OUT = os.path.expanduser("~/validation/k3s-faults")
STALL_S = 120
FREEZE_S = 90
LOAD_V = 20000          # base vertices
LOAD_E_PER_V = 5        # edges per vertex (sort key seq)
BATCH = 500


# ----------------------------------------------------------------------------- k8s / http helpers
def sh(cmd, check=True, timeout=120):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"{cmd}\n{r.stderr.strip()[:400]}")
    return r.stdout.strip()


def kc(args, check=True, timeout=120):
    return sh(f"kubectl -n {NS} {args}", check=check, timeout=timeout)


def secret(name, key):
    return base64.b64decode(kc(f"get secret {name} -o jsonpath='{{.data.{key}}}'")).decode()


def pods(selector=""):
    js = json.loads(kc(f"get pods {selector} -o json"))
    out = {}
    for p in js["items"]:
        cs = p["status"].get("containerStatuses") or [{}]
        out[p["metadata"]["name"]] = {
            "uid": p["metadata"]["uid"], "ip": p["status"].get("podIP"), "node": p["spec"].get("nodeName"),
            "phase": p["status"].get("phase"), "ready": all(c.get("ready") for c in cs),
            "restarts": sum(c.get("restartCount", 0) for c in cs),
        }
    return out


def java_pid(pod, container):
    return kc(f"exec {pod} -c {container} -- sh -c \"for p in /proc/[0-9]*; do grep -q java \\$p/cmdline "
              f"2>/dev/null && echo \\${{p#/proc/}}; done | head -1\"")


def signal_java(pod, container, sig):
    pid = java_pid(pod, container)
    kc(f"exec {pod} -c {container} -- kill -{sig} {pid}")
    return pid


def proc_state(pod, container, pid):
    return kc(f"exec {pod} -c {container} -- sh -c 'cut -d\" \" -f3 /proc/{pid}/stat'", check=False)


class Rest:
    def __init__(self):
        self.pw = secret("hugegraph-admin", "password")
        self.svc = kc("get svc hugegraph-server -o jsonpath='{.spec.clusterIP}'")
        self.auth = "Basic " + base64.b64encode(f"admin:{self.pw}".encode()).decode()

    def call(self, method, path, body=None, host=None, timeout=60):
        url = f"http://{host or self.svc}:8080{path}"
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json", "Authorization": self.auth,
                                              "Accept-Encoding": "identity", "Connection": "close"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                b = r.read(); st = r.status
        except urllib.error.HTTPError as e:
            b = e.read(); st = e.code
        except Exception as e:
            return 0, str(e)[:200], time.time() - t0
        if b[:2] == b"\x1f\x8b":
            b = gzip.decompress(b)
        return st, b.decode(errors="replace"), time.time() - t0

    def g(self, path):
        return self.call("GET", "/graphs/hugegraph" + path)

    def post(self, path, body, timeout=120):
        return self.call("POST", "/graphs/hugegraph" + path, body, timeout=timeout)


def pd_leader():
    key = secret("hugegraph-pd-auth", "secret-key")
    auth = "Basic " + base64.b64encode(f"hg:{key}".encode()).decode()
    for name, p in pods("-l app.kubernetes.io/component=pd").items():
        if not p["ip"]:
            continue
        req = urllib.request.Request(f"http://{p['ip']}:8620/v1/members",
                                     headers={"Authorization": auth, "Connection": "close"})
        try:
            with urllib.request.urlopen(req, timeout=4) as r:
                d = json.loads(r.read())
            leader = d.get("data", {}).get("pdLeader", {}).get("raftUrl", "")
            return leader.split(".")[0] if leader else None, name
        except Exception:
            continue
    return None, None


def partition_leaders():
    """{store pod name: number of partitions it leads} from PD /v1/partitions."""
    key = secret("hugegraph-pd-auth", "secret-key")
    auth = "Basic " + base64.b64encode(f"hg:{key}".encode()).decode()
    for name, p in pods("-l app.kubernetes.io/component=pd").items():
        if not p["ip"]:
            continue
        req = urllib.request.Request(f"http://{p['ip']}:8620/v1/partitions",
                                     headers={"Authorization": auth, "Connection": "close"})
        try:
            with urllib.request.urlopen(req, timeout=6) as r:
                d = json.loads(r.read())
        except Exception:
            continue
        count = {}
        for part in d.get("data", {}).get("partitions", []):
            for sh_ in part.get("shards", []):
                if sh_.get("role") == "Leader":
                    st = sh_.get("address", "?").split(".")[0]
                    count[st] = count.get(st, 0) + 1
        return count
    return {}


def busiest_store():
    lead = partition_leaders()
    if not lead:
        return "hugegraph-store-1", lead
    return max(lead.items(), key=lambda kv: kv[1])[0], lead


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(os.path.join(OUT, "battery.log"), "a") as f:
        f.write(line + "\n")


# ----------------------------------------------------------------------------- data / oracle
def vname(i):
    return f"v{i:07d}"


def vq(oracle, name):
    return urllib.request.quote(json.dumps(f"{oracle['vp']}{name}"), safe="")


def setup(rest):
    schema = [("/schema/propertykeys", {"name": "name", "data_type": "TEXT", "cardinality": "SINGLE"}),
              ("/schema/propertykeys", {"name": "seq", "data_type": "INT", "cardinality": "SINGLE"}),
              ("/schema/propertykeys", {"name": "w", "data_type": "INT", "cardinality": "SINGLE"}),
              ("/schema/vertexlabels", {"name": "node", "id_strategy": "PRIMARY_KEY", "primary_keys": ["name"],
                                        "properties": ["name", "w"], "nullable_keys": ["w"]}),
              ("/schema/edgelabels", {"name": "link", "source_label": "node", "target_label": "node",
                                      "frequency": "MULTIPLE", "sort_keys": ["seq"], "properties": ["seq", "w"],
                                      "nullable_keys": ["w"]})]
    for path, body in schema:
        st, b, _ = rest.post(path, body)
        if st not in (201, 202):
            raise SystemExit(f"schema {body['name']}: HTTP {st} {b[:200]}")
    # warm every server through a REST property filter before anything else touches Gremlin (fresh-server trap)
    for host in [p["ip"] for p in pods("-l app.kubernetes.io/component=server").values()]:
        rest.call("GET", "/graphs/hugegraph/graph/vertices?label=node&properties=%7B%22w%22%3A1%7D&limit=1",
                  host=host)
    t0 = time.time()
    for i in range(0, LOAD_V, BATCH):
        vs = [{"label": "node", "properties": {"name": vname(j), "w": j % 97}} for j in range(i, min(i + BATCH, LOAD_V))]
        st, b, _ = rest.post("/graph/vertices/batch", vs)
        if st != 201:
            raise SystemExit(f"vertex batch {i}: HTTP {st} {b[:200]}")
        if i == 0:
            first = json.loads(b)[0]; vp = first[:len(first) - len(vname(0))]  # e.g. "1:"
    log(f"setup: {LOAD_V} vertices in {time.time() - t0:.0f}s")
    t0 = time.time(); rnd = random.Random(11); edges = []
    for i in range(LOAD_V):
        for s in range(LOAD_E_PER_V):
            edges.append((vname(i), vname(rnd.randrange(LOAD_V)), s))
    for i in range(0, len(edges), BATCH):
        es = [{"label": "link", "outV": f"{vp}{a}", "inV": f"{vp}{b}", "outVLabel": "node", "inVLabel": "node",
               "properties": {"seq": s, "w": s}} for a, b, s in edges[i:i + BATCH]]
        st, b, _ = rest.post("/graph/edges/batch", es)
        if st != 201:
            raise SystemExit(f"edge batch {i}: HTTP {st} {b[:200]}")
    log(f"setup: {len(edges)} edges in {time.time() - t0:.0f}s")
    oracle = {"vertices": LOAD_V, "edges_per_vertex": LOAD_E_PER_V, "edges": len(edges), "seed": 11,
              "vp": vp, "acked": {}}
    json.dump(oracle, open(os.path.join(OUT, "oracle.json"), "w"))
    return oracle


def verify(rest, oracle, sample=400, tag="verify"):
    """Sampled exact check: vertex present with its properties, outE count and seq set as generated."""
    rnd = random.Random(11); adj = {}
    for i in range(oracle["vertices"]):
        adj[i] = [(rnd.randrange(oracle["vertices"]), s) for s in range(oracle["edges_per_vertex"])]
    pick = random.Random(int(time.time())).sample(range(oracle["vertices"]), sample)
    bad = []
    for i in pick:
        st, b, _ = rest.g(f"/graph/vertices/{vq(oracle, vname(i))}")
        if st != 200 or json.loads(b).get("properties", {}).get("w") != i % 97:
            bad.append((vname(i), "vertex", st, b[:80])); continue
        st, b, _ = rest.g(f"/graph/edges?vertex_id={vq(oracle, vname(i))}&direction=OUT&label=link&limit=1000")
        if st != 200:
            bad.append((vname(i), "edges", st, b[:80])); continue
        got = sorted((e["inV"], e["properties"]["seq"]) for e in json.loads(b)["edges"])
        want = sorted((f"{oracle['vp']}{vname(t)}", s) for t, s in adj[i])
        if got != want:
            bad.append((vname(i), "edgeset", len(got), len(want)))
    # every write acknowledged during earlier faults must be readable now
    lost = []
    for name, w in oracle.get("acked", {}).items():
        st, b, _ = rest.g(f"/graph/vertices/{vq(oracle, name)}")
        if st != 200 or json.loads(b).get("properties", {}).get("w") != w:
            lost.append((name, st))
    log(f"{tag}: sampled {sample} vertices, {len(bad)} mismatches; acked writes {len(oracle.get('acked', {}))}, "
        f"lost {len(lost)}")
    return bad, lost


# ----------------------------------------------------------------------------- load during faults
class Load(threading.Thread):
    """One writer (batch of 20 new vertices per request) and one reader (random oracle vertex + its edges)."""

    def __init__(self, rest, oracle, tag):
        super().__init__(daemon=True)
        self.rest, self.oracle, self.tag = rest, oracle, tag
        self.stop = threading.Event(); self.events = []; self.n = 0
        self.acked = {}

    def run(self):
        rnd = random.Random()
        while not self.stop.is_set():
            self.n += 1
            names = [f"{self.tag}-{self.n:06d}-{k}" for k in range(20)]
            body = [{"label": "node", "properties": {"name": nm, "w": (self.n + k) % 97}} for k, nm in enumerate(names)]
            st, b, dt = self.rest.post("/graph/vertices/batch", body, timeout=130)
            self.events.append((time.time(), "w", st, dt))
            if st == 201:
                for k, nm in enumerate(names):
                    self.acked[nm] = (self.n + k) % 97
            i = rnd.randrange(self.oracle["vertices"])
            st, b, dt = self.rest.g(f"/graph/edges?vertex_id={vq(self.oracle, vname(i))}&direction=OUT&label=link&limit=100")
            ok = st == 200 and len(json.loads(b)["edges"]) == self.oracle["edges_per_vertex"] if st == 200 else False
            self.events.append((time.time(), "r", st if ok or st != 200 else 599, dt))
            time.sleep(0.2)

    def summary(self, t_fault, t_end):
        def stats(kind):
            ev = [e for e in self.events if e[1] == kind and t_fault <= e[0] <= t_end]
            bad = [e for e in ev if e[2] not in (200, 201)]
            first_bad = min((e[0] for e in bad), default=None); last_bad = max((e[0] for e in bad), default=None)
            return {"requests": len(ev), "failed": len(bad),
                    "first_failure_s": None if first_bad is None else round(first_bad - t_fault, 1),
                    "last_failure_s": None if last_bad is None else round(last_bad - t_fault, 1),
                    "max_latency_s": round(max((e[3] for e in ev), default=0), 1),
                    "codes": sorted({e[2] for e in bad})}
        return {"writes": stats("w"), "reads": stats("r")}


def wait_ready(selector, want, timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        ps = pods(selector)
        if len(ps) == want and all(p["ready"] for p in ps.values()):
            return time.time() - t0
        time.sleep(3)
    return None


def result(name, verdict, blame, evidence, load_summary=None, extra=None):
    r = {"scenario": name, "verdict": verdict, "blame": blame, "evidence": evidence, "load": load_summary,
         "extra": extra, "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(os.path.join(OUT, f"{name}.json"), "w") as f:
        json.dump(r, f, indent=1)
    log(f"RESULT {name}: {verdict} ({blame}) {evidence}")
    return r


# ----------------------------------------------------------------------------- scenarios
def store_crash(rest, oracle):
    victim, lead = busiest_store()
    before = pods("-l app.kubernetes.io/component=store")[victim]
    log(f"store-crash: partition leaders {lead}, victim {victim}")
    leader0, _ = pd_leader()
    ld = Load(rest, oracle, "crash"); ld.start(); time.sleep(20)
    t_fault = time.time(); log(f"store-crash: deleting {victim} (uid {before['uid'][:8]}, node {before['node']})")
    kc(f"delete pod {victim} --grace-period=0 --force", timeout=60)
    back = wait_ready("-l app.kubernetes.io/component=store", 3, 600)
    time.sleep(60); ld.stop.set(); ld.join(); t_end = time.time()
    after = pods("-l app.kubernetes.io/component=store")[victim]
    landed = after["uid"] != before["uid"]
    oracle["acked"].update(ld.acked); json.dump(oracle, open(os.path.join(OUT, "oracle.json"), "w"))
    bad, lost = verify(rest, oracle, tag="store-crash/after")
    s = ld.summary(t_fault, t_end)
    ev = {"victim": victim, "leaders_before": lead, "leaders_after": partition_leaders(),
          "uid_before": before["uid"], "uid_after": after["uid"], "node_after": after["node"],
          "store_ready_again_s": None if back is None else round(back, 0), "pd_leader": leader0,
          "verify_mismatches": len(bad), "acked_lost": len(lost)}
    if not landed:
        return result("store-crash", "INCONCLUSIVE", "harness", ev, s)
    verdict = "PASS" if (back is not None and not bad and not lost) else "FAIL"
    return result("store-crash", verdict, "image" if verdict == "FAIL" else "chart", ev, s)


def store_stall(rest, oracle):
    victim, lead = busiest_store()
    before = pods("-l app.kubernetes.io/component=store")[victim]
    log(f"store-stall: partition leaders {lead}, victim {victim}")
    ld = Load(rest, oracle, "stall"); ld.start(); time.sleep(20)
    t_fault = time.time(); pid = signal_java(victim, "store", "STOP")
    state = proc_state(victim, "store", pid)
    log(f"store-stall: SIGSTOP java pid {pid} in {victim}, /proc state {state!r}, holding {STALL_S}s")
    time.sleep(STALL_S)
    kc(f"exec {victim} -c store -- kill -CONT {pid}"); t_cont = time.time()
    log("store-stall: SIGCONT sent")
    time.sleep(90); ld.stop.set(); ld.join(); t_end = time.time()
    after = pods("-l app.kubernetes.io/component=store")[victim]
    oracle["acked"].update(ld.acked); json.dump(oracle, open(os.path.join(OUT, "oracle.json"), "w"))
    bad, lost = verify(rest, oracle, tag="store-stall/after")
    s = ld.summary(t_fault, t_end)
    # per-phase: during the stall vs after SIGCONT
    during = Load.summary(ld, t_fault, t_cont); post = Load.summary(ld, t_cont, t_end)
    ev = {"victim": victim, "leaders_before": lead, "leaders_after": partition_leaders(),
          "java_pid": pid, "proc_state_after_stop": state, "stall_s": STALL_S,
          "restarts_before": before["restarts"], "restarts_after": after["restarts"],
          "uid_changed": after["uid"] != before["uid"], "verify_mismatches": len(bad), "acked_lost": len(lost)}
    if state.strip() != "T":
        return result("store-stall", "INCONCLUSIVE", "harness", ev, s, {"during": during, "after_cont": post})
    verdict = "PASS" if (not bad and not lost) else "FAIL"
    return result("store-stall", verdict, "image" if verdict == "FAIL" else "chart", ev, s,
                  {"during": during, "after_cont": post})


def pd_restart(rest, oracle):
    leader, via = pd_leader()
    if not leader:
        return result("pd-restart", "INCONCLUSIVE", "harness", {"pd_leader": None})
    before = pods("-l app.kubernetes.io/component=pd")[leader]
    servers = pods("-l app.kubernetes.io/component=server")
    ld = Load(rest, oracle, "pdr"); ld.start(); time.sleep(20)
    t_fault = time.time(); log(f"pd-restart: deleting PD leader {leader} (uid {before['uid'][:8]})")
    kc(f"delete pod {leader} --grace-period=0 --force", timeout=60)
    t_new = None
    for _ in range(120):
        l2, _ = pd_leader()
        if l2 and l2 != leader:
            t_new = time.time() - t_fault; break
        time.sleep(1)
    back = wait_ready("-l app.kubernetes.io/component=pd", 3, 600)
    time.sleep(30); ld.stop.set(); ld.join(); t_end = time.time()
    # F11 shape: schema through one server, visible through the others?
    ips = [p["ip"] for p in servers.values()]
    pk = f"pk_after_pd_{int(time.time())}"
    st, b, _ = rest.call("POST", "/graphs/hugegraph/schema/propertykeys",
                         {"name": pk, "data_type": "INT", "cardinality": "SINGLE"}, host=ips[0])
    seen = {}
    for ip in ips[1:]:
        for _ in range(30):
            st2, b2, _ = rest.call("GET", f"/graphs/hugegraph/schema/propertykeys/{pk}", host=ip, timeout=10)
            if st2 == 200:
                break
            time.sleep(1)
        seen[ip] = st2
    after = pods("-l app.kubernetes.io/component=pd")[leader]
    oracle["acked"].update(ld.acked); json.dump(oracle, open(os.path.join(OUT, "oracle.json"), "w"))
    bad, lost = verify(rest, oracle, tag="pd-restart/after")
    s = ld.summary(t_fault, t_end)
    ev = {"leader_before": leader, "uid_before": before["uid"], "uid_after": after["uid"],
          "new_leader_after_s": None if t_new is None else round(t_new, 1), "pd_ready_again_s": back,
          "schema_create_http": st, "schema_seen_on_other_servers": seen,
          "verify_mismatches": len(bad), "acked_lost": len(lost)}
    if after["uid"] == before["uid"]:
        return result("pd-restart", "INCONCLUSIVE", "harness", ev, s)
    ok = st in (201, 202) and all(v == 200 for v in seen.values()) and not bad and not lost and t_new is not None
    return result("pd-restart", "PASS" if ok else "FAIL", "image" if not ok else "chart", ev, s)


def pd_freeze(rest, oracle):
    leader, _ = pd_leader()
    if not leader:
        return result("pd-freeze", "INCONCLUSIVE", "harness", {"pd_leader": None})
    uid0 = pods("-l app.kubernetes.io/component=pd")[leader]["uid"]
    ld = Load(rest, oracle, "pdf"); ld.start(); time.sleep(20)
    t_fault = time.time(); pid = signal_java(leader, "pd", "STOP"); state = proc_state(leader, "pd", pid)
    log(f"pd-freeze: SIGSTOP java pid {pid} in PD leader {leader}, state {state!r}, {FREEZE_S}s")
    samples = []; t_new = None
    while time.time() - t_fault < FREEZE_S:
        l2, via = pd_leader(); samples.append((round(time.time() - t_fault, 1), l2, via))
        if l2 and l2 != leader and t_new is None:
            t_new = time.time() - t_fault
        time.sleep(2)
    kc(f"exec {leader} -c pd -- kill -CONT {pid}"); t_cont = time.time(); log("pd-freeze: SIGCONT sent")
    time.sleep(60); ld.stop.set(); ld.join(); t_end = time.time()
    after = pods("-l app.kubernetes.io/component=pd")[leader]
    oracle["acked"].update(ld.acked); json.dump(oracle, open(os.path.join(OUT, "oracle.json"), "w"))
    bad, lost = verify(rest, oracle, tag="pd-freeze/after")
    before_uid = uid0
    s = ld.summary(t_fault, t_end)
    leaderless = [t for t, l, _ in samples if l is None]
    ev = {"leader_before": leader, "java_pid": pid, "proc_state_after_stop": state, "freeze_s": FREEZE_S,
          "new_leader_after_s": None if t_new is None else round(t_new, 1),
          "leaderless_samples": len(leaderless), "leaderless_first_s": leaderless[0] if leaderless else None,
          "leaderless_last_s": leaderless[-1] if leaderless else None,
          "restarts_after": after["restarts"], "uid_changed": after["uid"] != before_uid,
          "verify_mismatches": len(bad), "acked_lost": len(lost)}
    if state.strip() != "T":
        return result("pd-freeze", "INCONCLUSIVE", "harness", ev, s, {"samples": samples})
    verdict = "PASS" if (t_new is not None and not bad and not lost) else "FAIL"
    return result("pd-freeze", verdict, "image" if verdict == "FAIL" else "chart", ev, s, {"samples": samples})


def cold_start(rest, oracle):
    servers0 = pods("-l app.kubernetes.io/component=server")
    log("cold-start: scaling stores to 0")
    kc("scale sts hugegraph-store --replicas=0"); t0 = time.time()
    for _ in range(120):
        if not pods("-l app.kubernetes.io/component=store"):
            break
        time.sleep(2)
    log("cold-start: rolling the server deployment with no stores")
    kc("rollout restart deploy hugegraph-server"); t_roll = time.time()
    time.sleep(150)
    mid = pods("-l app.kubernetes.io/component=server")
    log(f"cold-start: after 150s without stores: " +
        ", ".join(f"{n}:{p['phase']}/ready={p['ready']}/restarts={p['restarts']}" for n, p in mid.items()))
    kc("scale sts hugegraph-store --replicas=3")
    back = wait_ready("-l app.kubernetes.io/component=store", 3, 600)
    srv = wait_ready("-l app.kubernetes.io/component=server", 3, 600)
    after = pods("-l app.kubernetes.io/component=server")
    new_pods = {n: p for n, p in after.items() if n not in servers0}
    bad, lost = verify(rest, oracle, tag="cold-start/after")
    ev = {"stores_scaled_down": True, "servers_after_150s_without_stores":
          {n: (p["phase"], p["ready"], p["restarts"]) for n, p in mid.items()},
          "stores_ready_again_s": back, "servers_ready_s_after_stores": srv,
          "new_server_restarts": {n: p["restarts"] for n, p in new_pods.items()},
          "startup_timeout_env": kc("get deploy hugegraph-server -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name==\"HG_SERVER_STARTUP_TIMEOUT_S\")].value}'"),
          "verify_mismatches": len(bad), "acked_lost": len(lost)}
    ok = srv is not None and all(p["restarts"] == 0 for p in new_pods.values()) and not bad and not lost
    return result("cold-start", "PASS" if ok else "FAIL", "image" if not ok else "chart", ev)


# ----------------------------------------------------------------------------- main
def main():
    global NS, OUT
    ap = argparse.ArgumentParser(); ap.add_argument("scenario"); ap.add_argument("--ns", default=NS)
    ap.add_argument("--out", default=OUT); a = ap.parse_args()
    NS, OUT = a.ns, os.path.expanduser(a.out); os.makedirs(OUT, exist_ok=True)
    rest = Rest()
    if a.scenario == "setup":
        setup(rest); return
    oracle = json.load(open(os.path.join(OUT, "oracle.json")))
    log(f"--- {a.scenario}: pods " + ", ".join(f"{n}({p['node']},r{p['restarts']})" for n, p in pods().items())
        + f"; PD leader {pd_leader()[0]}")
    fn = {"verify": lambda r, o: verify(r, o), "store-crash": store_crash, "store-stall": store_stall,
          "pd-restart": pd_restart, "pd-freeze": pd_freeze, "cold-start": cold_start}[a.scenario]
    fn(rest, oracle)


if __name__ == "__main__":
    main()
