#!/usr/bin/env python3
"""Validate #3164 (snapshot save during compaction, apache/hugegraph#3162) on pods.

Per partition of one Store pod: submit a compaction through the Store REST (async, ~1 s on a 19 MB
partition), request a raft snapshot of every partition `gap` ms later, then SIGKILL the Store JVM inside the
container (a crash mid-compaction), let kubelet restart it and check that every snapshot directory of the
partition has both `__raft_snapshot_meta` and `data/`, that the partition is back to PState_Normal and that the
log has no "snapshot is corrupt". The race is proven to have fired by the #3164 log line
"snapshot save failed: compaction in progress" (EBUSY to jraft) between the compaction start and the kill.

  python3 k3s_3164.py <store-pod> <partition-ids comma-separated> [gap-ms=300]
"""
import json, os, subprocess, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import k3s_faults as F  # noqa: E402

OUT = os.path.expanduser("~/validation/k3s-3164")
NS = "hugegraph"
POD, PARTS = sys.argv[1], [int(x) for x in sys.argv[2].split(",")]
GAPS = [int(x) for x in (sys.argv[3] if len(sys.argv) > 3 else "300").split(",")]
GAP_MS = GAPS[0]


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(os.path.join(OUT, "3164.log"), "a") as f:
        f.write(line + "\n")


def pod_ip():
    return F.kc(f"get pod {POD} -o jsonpath='{{.status.podIP}}'").replace("'", "")


def rest(method, path, timeout=60):
    req = urllib.request.Request(f"http://{pod_ip()}:8520{path}", method=method,
                                 headers={"Connection": "close"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")[:200], time.time() - t0
    except Exception as e:
        return 0, str(e)[:200], time.time() - t0


def snapshot_dirs(part):
    p = f"{part:05d}"
    out = F.kc(f"exec {POD} -c store -- sh -c 'for d in storage/raft/{p}/snapshot/*/; do "
               f"n=$(basename $d); m=$([ -f $d/__raft_snapshot_meta ] && echo yes || echo NO); "
               f"dd=$([ -d $d/data ] && echo $(ls $d/data | wc -l) || echo NO); "
               f"s=$([ -f $d/should_not_load ] && echo yes || echo no); echo $n meta=$m data=$dd should_not_load=$s; done'",
               check=False)
    return [x for x in out.splitlines() if x.strip()]


def partition_state(part):
    st, body, _ = rest("GET", f"/v1/partition/{part}")
    try:
        return [(p["id"], p["workState"]) for p in json.loads(body)["partitions"]]
    except Exception:
        return body


def store_log(since):
    return F.kc(f"logs {POD} -c store --since={since}", check=False)


def attempt(part):
    log(f"=== partition {part} on {POD}, gap {GAP_MS} ms")
    log(f"snapshots before: {snapshot_dirs(part)}")
    t_start = time.time()
    st, body, _ = rest("POST", f"/v1/compat?id={part}")
    log(f"compaction submit: {st} {body}")
    time.sleep(GAP_MS / 1000.0)
    st, body, dt = rest("GET", "/test/snapshot")
    log(f"snapshot request: {st} {body} in {dt:.2f}s")
    time.sleep(1.0)
    during = snapshot_dirs(part)
    log(f"snapshots during compaction: {during}")
    # the #3164 line is written by the JVM about to be killed: read it now
    pre_kill = store_log(f"{int(time.time() - t_start) + 5}s")
    pid = F.signal_java(POD, "store", "KILL")
    t_kill = time.time()
    log(f"SIGKILL java pid {pid} at +{t_kill - t_start:.1f}s")
    time.sleep(3)
    after_kill = snapshot_dirs(part)
    log(f"snapshots after the kill: {after_kill}")
    back = F.wait_ready(f"-l statefulset.kubernetes.io/pod-name={POD}", 1, 600)
    log(f"{POD} Ready again after {back}s")
    time.sleep(30)
    state = partition_state(part)
    final = snapshot_dirs(part)
    lg = store_log(f"{int(time.time() - t_start) + 5}s")
    prev = F.kc(f"logs {POD} -c store --previous", check=False)
    busy = [l for l in (pre_kill + "\n" + prev).splitlines()
            if "compaction in progress" in l or "EBUSY" in l]
    compaction = [l for l in (pre_kill + "\n" + prev).splitlines()
                  if f"Partition {part}" in l and "dbCompaction" in l]
    corrupt = [l for l in lg.splitlines() if "snapshot is corrupt" in l or "data dir" in l and "missing" in l]
    errors = [l for l in lg.splitlines() if "[ERROR]" in l]
    meta_only = [d for d in final if "meta=yes" in d and "data=NO" in d]
    r = {"partition": part, "pod": POD, "gap_ms": GAP_MS, "snapshots_during": during,
         "snapshots_after_kill": after_kill, "snapshots_final": final, "state_after": state,
         "ready_again_s": back, "race_fired": bool(busy), "busy_lines": busy[:3],
         "compaction_lines": [c[:160] for c in compaction[-4:]],
         "corrupt_lines": corrupt[:3], "error_count": len(errors), "errors": [e[:200] for e in errors[:5]],
         "meta_only_dirs": meta_only}
    if not busy:
        verdict = "INCONCLUSIVE"
    else:
        ok = not meta_only and not corrupt and back is not None and \
             any(s == "PState_Normal" for _, s in (state if isinstance(state, list) else []))
        verdict = "PASS" if ok else "FAIL"
    r["verdict"] = verdict
    with open(os.path.join(OUT, f"partition-{part}.json"), "w") as f:
        json.dump(r, f, indent=1, default=str)
    log(f"RESULT partition {part}: {verdict} race_fired={bool(busy)} meta_only={meta_only} "
        f"corrupt={len(corrupt)} state={[s for _, s in state] if isinstance(state, list) else state} "
        f"errors={len(errors)} compaction={[c[11:19] + c[c.find('Partition'):][:60] for c in compaction[-3:]]}")
    return r


def main():
    os.makedirs(OUT, exist_ok=True)
    F.wait_ready("-l app.kubernetes.io/component=store", 3, 300)
    global GAP_MS
    for i, part in enumerate(PARTS):
        GAP_MS = GAPS[i % len(GAPS)]
        attempt(part)
        time.sleep(10)


if __name__ == "__main__":
    main()
