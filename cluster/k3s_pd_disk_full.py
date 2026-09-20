#!/usr/bin/env python3
"""Full PD disk on the single preset (1 PD + 1 Store + 1 Server), Helm chart of apache/hugegraph#3218 / #221.

PD runs on a 512 MB loop-backed ext4 (hostPath PV), so ENOSPC is real and the node disk is untouched.
Phases, all sampled at 1 Hz per second on PD (/v1/ready, /v1/health, pod Ready) and Server (/readiness,
one schema write + one vertex read through the Service):
  baseline 60 s -> fill (fallocate inside pd_data until ENOSPC) 180 s -> free (rm filler) 180 s ->
  if PD is still not ready: restart the PD pod, 120 s -> verify the oracle.

  python3 k3s_pd_disk_full.py setup   # schema + small oracle in the hg-single namespace
  python3 k3s_pd_disk_full.py run
"""
import base64, json, os, sys, threading, time, urllib.request, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import k3s_faults as F  # noqa: E402

NS = "hg-single"
REL = "hg-single-hugegraph"
OUT = os.path.expanduser("~/validation/k3s-pd-disk-full")
F.NS = NS
F.OUT = OUT
F.LOAD_V, F.LOAD_E_PER_V = 2000, 3
PD_DATA = "/hugegraph-pd/pd_data"


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(os.path.join(OUT, "pd-disk-full.log"), "a") as f:
        f.write(line + "\n")


def http(url, timeout=4, auth=None):
    h = {"Connection": "close", "Accept-Encoding": "identity"}
    if auth:
        h["Authorization"] = auth
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except Exception as e:
        return 0, str(e)[:100]


def pod_ip(name):
    return F.kc(f"get pod {name} -o jsonpath='{{.status.podIP}}'", check=False).replace("'", "")


def pod_ready(name):
    return F.kc(f"get pod {name} -o jsonpath='{{.status.containerStatuses[0].ready}} {{.status.containerStatuses[0].restartCount}}'",
                check=False).replace("'", "")


def pd_disk():
    return F.kc(f"exec {REL}-pd-0 -c pd -- sh -c 'df -k {PD_DATA} | tail -1'", check=False).split()


class Sampler(threading.Thread):

    def __init__(self, rest, oracle):
        super().__init__(daemon=True)
        self.rest, self.oracle, self.rows, self.stop = rest, oracle, [], threading.Event()
        self.t0 = time.time()
        self.pd_auth = "Basic " + base64.b64encode(
            f"hg:{F.secret('hg-single-pd-auth', 'secret-key')}".encode()).decode()

    def run(self):
        n = 0
        while not self.stop.is_set():
            n += 1
            t = round(time.time() - self.t0, 1)
            pd_ip, sv_ip = pod_ip(f"{REL}-pd-0"), pod_ip(f"{REL}-server-0") or ""
            if not sv_ip:
                sv = F.kc(f"get pods -l app.kubernetes.io/component=server -o jsonpath='{{.items[0].status.podIP}}'", check=False).replace("'", "")
                sv_ip = sv
            r = {"t": t}
            r["pd_ready_http"], b = http(f"http://{pd_ip}:8620/v1/ready", auth=self.pd_auth) if pd_ip else (0, "")
            r["pd_ready_body"] = b[:80]
            r["pd_health"], _ = http(f"http://{pd_ip}:8620/v1/health", auth=self.pd_auth) if pd_ip else (0, "")
            r["pd_pod"] = pod_ready(f"{REL}-pd-0")
            r["srv_readiness"], b = http(f"http://{sv_ip}:8080/readiness") if sv_ip else (0, "")
            try:
                j = json.loads(b); r["srv_reason"] = j.get("reason"); r["srv_pd_reachable"] = j.get("pd_reachable")
            except Exception:
                r["srv_reason"] = b[:60]
            # one write (a throwaway property key) and one read every 5th sample
            if n % 5 == 0:
                st, wb, dt = self.rest.post("/schema/propertykeys", {"name": f"pdfull_{n}", "data_type": "INT", "cardinality": "SINGLE"}, timeout=20)
                r["write"] = st; r["write_s"] = round(dt, 1)
                st, rb, dt = self.rest.g(f"/graph/vertices/{F.vq(self.oracle, F.vname(1))}")
                r["read"] = st; r["read_s"] = round(dt, 1)
            d = pd_disk()
            r["pd_disk_used_pct"] = d[4] if len(d) > 4 else None
            self.rows.append(r)
            time.sleep(max(0.0, 1.0 - ((time.time() - self.t0) % 1.0)))

    def finish(self):
        self.stop.set(); self.join(timeout=15)
        with open(os.path.join(OUT, "samples.json"), "w") as f:
            json.dump(self.rows, f)
        return self.rows


def phase_summary(rows, a, b):
    rs = [r for r in rows if a <= r["t"] < b]
    def codes(k): 
        c = {}
        for r in rs:
            if k in r: c[r[k]] = c.get(r[k], 0) + 1
        return c
    return {"samples": len(rs), "pd_ready": codes("pd_ready_http"), "pd_health": codes("pd_health"),
            "pd_pod": codes("pd_pod"), "srv_readiness": codes("srv_readiness"),
            "srv_reasons": sorted({str(r.get("srv_reason"))[:50] for r in rs}),
            "writes": codes("write"), "reads": codes("read"),
            "disk_used_pct": sorted({r.get("pd_disk_used_pct") for r in rs if r.get("pd_disk_used_pct")})[-3:]}


def main():
    os.makedirs(OUT, exist_ok=True)
    # the fault harness hardcodes the 'hugegraph' release names; build the client for this release
    rest = F.Rest.__new__(F.Rest)
    rest.pw = F.secret("hg-single-admin", "password")
    rest.svc = F.kc(f"get svc {REL}-server -o jsonpath='{{.spec.clusterIP}}'").replace("'", "")
    rest.auth = "Basic " + base64.b64encode(f"admin:{rest.pw}".encode()).decode()
    if sys.argv[1] == "setup":
        F.setup(rest); return
    oracle = json.load(open(os.path.join(OUT, "oracle.json"))); oracle.setdefault("acked", {})
    log(f"PD disk before: {' '.join(pd_disk())}")
    s = Sampler(rest, oracle); s.start()
    time.sleep(60); t_fill = round(time.time() - s.t0, 1)
    out = F.kc(f"exec {REL}-pd-0 -c pd -- sh -c 'fallocate -l 2G {PD_DATA}/filler 2>&1 || dd if=/dev/zero of={PD_DATA}/filler bs=1M 2>&1 | tail -1; df -k {PD_DATA} | tail -1'", check=False, timeout=120)
    log(f"fill at t={t_fill}: {out.strip()[:200]}")
    time.sleep(180); t_free = round(time.time() - s.t0, 1)
    out = F.kc(f"exec {REL}-pd-0 -c pd -- sh -c 'rm -f {PD_DATA}/filler; df -k {PD_DATA} | tail -1'", check=False, timeout=60)
    log(f"free at t={t_free}: {out.strip()[:200]}")
    time.sleep(180); t_end = round(time.time() - s.t0, 1)
    restarted = False
    last = s.rows[-1]
    if last.get("pd_ready_http") != 200:
        log(f"PD still not ready after freeing the disk ({last}); restarting the PD pod")
        F.kc(f"delete pod {REL}-pd-0 --grace-period=30", timeout=90); restarted = True
        F.wait_ready("-l app.kubernetes.io/component=pd", 1, 300)
        time.sleep(120)
    t_stop = round(time.time() - s.t0, 1)
    rows = s.finish()
    bad, lost = F.verify(rest, oracle, sample=200, tag="pd-disk-full/after")
    pd_log = F.kc(f"logs {REL}-pd-0 -c pd --tail=4000", check=False)
    prev = F.kc(f"logs {REL}-pd-0 -c pd --previous --tail=4000", check=False) if restarted else ""
    errs = [l[:200] for l in (pd_log + "\n" + prev).splitlines() if "No space left" in l or "ENOSPC" in l or "[ERROR]" in l]
    summary = {"baseline": phase_summary(rows, 0, t_fill), "full": phase_summary(rows, t_fill + 2, t_free),
               "freed": phase_summary(rows, t_free + 2, t_end), "after_restart": phase_summary(rows, t_end, t_stop) if restarted else None,
               "pd_restarted": restarted, "verify_mismatches": len(bad), "acked_lost": len(lost),
               "pd_error_lines": errs[:12], "pd_error_count": len(errs)}
    with open(os.path.join(OUT, "pd-disk-full.json"), "w") as f:
        json.dump(summary, f, indent=1, default=str)
    log("SUMMARY " + json.dumps(summary, default=str)[:1500])


if __name__ == "__main__":
    main()
