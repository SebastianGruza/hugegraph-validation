#!/usr/bin/env python3
"""decimal_e2e.py <rest_port> <gremlin_port> <tag> [name_prefix=d2_]
End-to-end checks for the DECIMAL property type on a running HugeGraph server (any backend), one graph
`hugegraph` in graphspace DEFAULT. Every value is compared exactly against Python's Decimal. Covers the paths
raised in the apache/hugegraph#3209 review on top of the REST batch path already covered by decimal_sum_bench.py:
  R1  schema: DECIMAL keys, a decimal default value in user_data, vertex label
  R2  no index of any type on a decimal: secondary / range index labels, decimal sort key
  R3  OLAP write types on a decimal key (OLAP_SECONDARY, OLAP_RANGE must be rejected; OLAP_COMMON allowed)
  R4  REST write + read: uint256 max, 18-digit fraction, negative; PUT /graph/vertices/batch SUM with two
      entries of one vertex in one request; the default value applied to a vertex created without the key
  R5  Gremlin through the server's REST proxy (/gremlin, application/json)
  R6  Gremlin straight on gremlin-server HTTP with Accept application/vnd.gremlin-v1.0+json, v2.0, v3.0:
      values(), g.inject(1.5) (a Groovy decimal literal, the pre-existing case), sum()
Prints one line per check (PASS/FAIL/N-A) and a JSON summary; exit code 1 if any FAIL."""
import gzip, json, sys, urllib.request, urllib.error
from decimal import Decimal, getcontext

getcontext().prec = 200
REST = int(sys.argv[1]); GREM = int(sys.argv[2]); TAG = sys.argv[3] if len(sys.argv) > 3 else "run"
P = sys.argv[4] if len(sys.argv) > 4 else "d2_"  # schema name prefix, so the suite can share a graph
BASE = f"http://127.0.0.1:{REST}/graphspaces/DEFAULT/graphs/hugegraph"
UINT256_MAX = Decimal(2) ** 256 - 1
WEI = Decimal("0.000000000000000001")
FEE_DEFAULT = Decimal("0.000000000000000001")
RESULTS = []


def http(method, url, body=None, headers=None, timeout=300):
    hdr = {"Content-Type": "application/json", "Accept-Encoding": "identity"}
    hdr.update(headers or {})
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 method=method, headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            b = r.read(); return r.status, (gzip.decompress(b) if b[:2] == b"\x1f\x8b" else b)
    except urllib.error.HTTPError as e:
        b = e.read(); return e.code, (gzip.decompress(b) if b[:2] == b"\x1f\x8b" else b)
    except Exception as e:  # connection level
        return 0, str(e).encode()


def rest(method, path, body=None):
    return http(method, BASE + path, body)


def jbody(b):
    try:
        return json.loads(b)
    except Exception:
        return {"raw": b.decode(errors="replace")[:300]}


def record(check, ok, detail):
    status = "PASS" if ok is True else ("N-A" if ok is None else "FAIL")
    RESULTS.append({"check": check, "status": status, "detail": detail})
    print(f"{status:4} {check:58} {detail}"[:220], flush=True)


def dstr(d):
    return format(d, "f")


# ---------------------------------------------------------------- R1 schema
def schema():
    for name, dt in ((P + "name", "TEXT"), (P + "balance", "DECIMAL"), (P + "fee", "DECIMAL"), (P + "seq", "INT"),
                     (P + "amount", "DECIMAL")):
        body = {"name": name, "data_type": dt, "cardinality": "SINGLE"}
        if name == P + "fee":
            body["user_data"] = {"~default_value": dstr(FEE_DEFAULT)}
        st, b = rest("POST", "/schema/propertykeys", body)
        if st not in (201, 202):
            record(f"R1 property key {name} {dt}", False, f"HTTP {st} {jbody(b).get('message', '')[:120]}")
            return False
    st, b = rest("GET", "/schema/propertykeys/" + P + "fee")
    ud = jbody(b).get("user_data", {})
    record("R1 decimal default value stored in user_data", ud.get("~default_value") == dstr(FEE_DEFAULT),
           f"user_data={ud}")
    st, b = rest("POST", "/schema/vertexlabels",
                 {"name": P + "acct", "id_strategy": "PRIMARY_KEY", "primary_keys": [P + "name"],
                  "properties": [P + "name", P + "balance", P + "fee"], "nullable_keys": [P + "balance", P + "fee"]})
    record("R1 vertex label with two decimal properties", st in (201, 202), f"HTTP {st}")
    st, b = rest("POST", "/schema/edgelabels",
                 {"name": P + "xfer", "source_label": P + "acct", "target_label": P + "acct", "frequency": "MULTIPLE",
                  "sort_keys": [P + "seq"], "properties": [P + "seq", P + "amount"], "nullable_keys": [P + "amount"]})
    record("R1 edge label with a decimal property behind an INT sort key", st in (201, 202), f"HTTP {st}")
    return True


# ---------------------------------------------------------------- R2 indexes / sort keys
def indexes():
    for idx_type in ("SECONDARY", "RANGE"):
        st, b = rest("POST", "/schema/indexlabels",
                     {"name": f"{P}acctBy{idx_type}", "base_type": "VERTEX_LABEL", "base_value": P + "acct",
                      "index_type": idx_type, "fields": [P + "balance"]})
        msg = jbody(b).get("message", "")
        record(f"R2 {idx_type} index on a decimal rejected", st == 400 and "decimal" in msg,
               f"HTTP {st} {msg[:90]}")
    st, b = rest("POST", "/schema/edgelabels",
                 {"name": P + "bad", "source_label": P + "acct", "target_label": P + "acct", "frequency": "MULTIPLE",
                  "sort_keys": [P + "amount"], "properties": [P + "amount"]})
    msg = jbody(b).get("message", "")
    record("R2 decimal sort key rejected", st == 400 and "decimal" in msg.lower(), f"HTTP {st} {msg[:90]}")


# ---------------------------------------------------------------- R3 OLAP write types
def olap():
    st, b = rest("POST", "/schema/propertykeys",
                 {"name": P + "olap_probe", "data_type": "DOUBLE", "cardinality": "SINGLE", "write_type": "OLAP_COMMON"})
    if st == 400 and "olap" in jbody(b).get("message", "").lower():
        record("R3 OLAP write types", None, f"backend has no OLAP properties: {jbody(b).get('message', '')[:80]}")
        return
    rest("DELETE", "/schema/propertykeys/" + P + "olap_probe")
    for wt in ("OLAP_SECONDARY", "OLAP_RANGE"):
        name = P + "rank_" + wt.lower()
        st, b = rest("POST", "/schema/propertykeys",
                     {"name": name, "data_type": "DECIMAL", "cardinality": "SINGLE", "write_type": wt})
        msg = jbody(b).get("message", "")
        if st in (201, 202):
            # the key (and, on an unfixed server, its *olap_by_<name> index) exist: report and clean up
            st2, b2 = rest("GET", f"/schema/indexlabels/*olap_by_{name}")
            record(f"R3 {wt} on a decimal rejected", False,
                   f"HTTP {st}: key created; index *olap_by_{name} GET -> {st2}")
            rest("DELETE", f"/schema/propertykeys/{name}")
        else:
            record(f"R3 {wt} on a decimal rejected", st == 400 and "decimal" in msg.lower(), f"HTTP {st} {msg[:100]}")
    st, b = rest("POST", "/schema/propertykeys",
                 {"name": P + "rank_common", "data_type": "DECIMAL", "cardinality": "SINGLE", "write_type": "OLAP_COMMON"})
    record("R3 OLAP_COMMON on a decimal allowed (no index)", st in (201, 202),
           f"HTTP {st} {jbody(b).get('message', '')[:80]}")


# ---------------------------------------------------------------- R4 REST write / read
def vertex(name, props):
    body = {"label": P + "acct", "properties": {P + "name": name}}
    body["properties"].update(props)
    return rest("POST", "/graph/vertices", body)


def get_props(vid):
    st, b = rest("GET", f"/graph/vertices/{urlq(vid)}")
    return st, jbody(b).get("properties", {})


def urlq(s):
    return urllib.request.quote(json.dumps(s), safe="")


def rest_rw():
    cases = {"a": UINT256_MAX - 2, "b": WEI, "c": Decimal("-1.50"), "d": Decimal("0")}
    ids = {}
    for name, val in cases.items():
        st, b = vertex(name, {P + "balance": dstr(val)})
        d = jbody(b); ids[name] = d.get("id")
        got = d.get("properties", {}).get(P + "balance")
        record(f"R4 REST create balance={dstr(val)[:24]}", st == 201 and got == dstr(val) and isinstance(got, str),
               f"HTTP {st} response balance={got!r}")
    for name, val in cases.items():
        st, props = get_props(ids[name])
        got = props.get(P + "balance")
        record(f"R4 REST read back {name}", got == dstr(val), f"balance={got!r}")
    # batch SUM: a += 1 (number literal), b gets two entries in one request (strings), c BIGGER
    st, b = rest("PUT", "/graph/vertices/batch",
                 {"vertices": [
                     {"label": P + "acct", "properties": {P + "name": "a", P + "balance": 1}},
                     {"label": P + "acct", "properties": {P + "name": "a", P + "balance": 1}},
                     {"label": P + "acct", "properties": {P + "name": "b", P + "balance": dstr(WEI)}},
                     {"label": P + "acct", "properties": {P + "name": "b", P + "balance": "0.000000000000000000"}}],
                  "update_strategies": {P + "balance": "SUM"}, "create_if_not_exist": True})
    record("R4 batch SUM accepted", st == 200, f"HTTP {st} {jbody(b) if st != 200 else ''}"[:160])
    expect = {"a": UINT256_MAX, "b": WEI * 2}
    for name, val in expect.items():
        st, props = get_props(ids[name])
        record(f"R4 batch SUM result {name} exact", props.get(P + "balance") == dstr(val),
               f"balance={props.get(P + 'balance')!r} expected {dstr(val)}")
    # default value applied when the key is absent
    st, b = vertex("e", {})
    d = jbody(b); ids["e"] = d.get("id")
    st, props = get_props(ids["e"])
    record("R4 decimal default value applied to a new vertex", props.get(P + "fee") == dstr(FEE_DEFAULT),
           f"fee={props.get(P + 'fee')!r}")
    return ids


# ---------------------------------------------------------------- R5 / R6 Gremlin
def unwrap(v):
    """GraphSON v2/v3 typed value -> python; untyped passes through."""
    if isinstance(v, dict) and "@type" in v:
        t, val = v["@type"], v.get("@value")
        if t in ("g:List", "g:Set"):
            return [unwrap(x) for x in val]
        if t == "g:Map":
            it = iter(val); return {unwrap(k): unwrap(x) for k, x in zip(it, it)}
        return (t, unwrap(val) if isinstance(val, (dict, list)) else val)
    if isinstance(v, list):
        return [unwrap(x) for x in v]
    return v


def gremlin_rest(script):
    st, b = http("POST", f"http://127.0.0.1:{REST}/gremlin",
                 {"gremlin": script, "bindings": {}, "language": "gremlin-groovy",
                  "aliases": {"graph": "DEFAULT-hugegraph", "g": "__g_DEFAULT-hugegraph"}})
    d = jbody(b)
    if st != 200:
        return st, (d.get("message") or d.get("exception") or str(d))[:160]
    return st, unwrap(d.get("result", {}).get("data"))


def gremlin_direct(script, accept):
    st, b = http("POST", f"http://127.0.0.1:{GREM}/",
                 {"gremlin": script, "bindings": {}, "language": "gremlin-groovy",
                  "aliases": {"graph": "DEFAULT-hugegraph", "g": "__g_DEFAULT-hugegraph"}},
                 headers={"Accept": accept})
    d = jbody(b)
    if st != 200:
        return st, (d.get("message") or d.get("exception") or str(d))[:160]
    return st, unwrap(d.get("result", {}).get("data"))


def expect_decimal(got, want, typed):
    """typed: v2/v3 -> ('gx:BigDecimal', '1.5' or 1.5); untyped v1 -> '1.5' string or number."""
    if isinstance(got, list) and len(got) == 1:
        got = got[0]
    if typed:
        if not (isinstance(got, tuple) and got[0] == "gx:BigDecimal"):
            return False, f"got {got!r}"
        val = got[1]
    else:
        val = got
    try:
        exact = Decimal(str(val)) == want
    except Exception:
        return False, f"got {got!r}"
    return exact, f"got {got!r}" + ("" if isinstance(val, str) else " (JSON number, not a string)")


def gremlin_checks(ids):
    va = json.dumps(ids["a"])
    scripts = {
        "values(balance) uint256 max": (f"g.V({va}).values('{P}balance')", UINT256_MAX),
        "g.inject(1.5) Groovy literal": ("g.inject(1.5)", Decimal("1.5")),
        "values(fee) default": (f"g.V({json.dumps(ids['e'])}).values('{P}fee')", FEE_DEFAULT),
        "sum() over balances": (f"g.V().hasLabel('{P}acct').values('{P}balance').sum()",
                                UINT256_MAX + WEI * 2 + Decimal("-1.50")),
    }
    for label, (script, want) in scripts.items():
        st, got = gremlin_rest(script)
        ok, det = (False, got) if st != 200 else expect_decimal(got, want, typed=isinstance(got, list) and got
                                                                                 and isinstance(got[0], tuple))
        record(f"R5 /gremlin REST proxy: {label}", ok, f"HTTP {st} {det}")
    for ver, accept in (("v1", "application/vnd.gremlin-v1.0+json"), ("v2", "application/vnd.gremlin-v2.0+json"),
                        ("v3", "application/vnd.gremlin-v3.0+json")):
        for label, (script, want) in scripts.items():
            st, got = gremlin_direct(script, accept)
            if st == 400 and isinstance(got, str) and "no serializer for requested Accept" in got:
                # the server's gremlin-server.yaml does not expose this Accept type: not a DECIMAL result
                record(f"R6 gremlin-server {ver}: {label}", None, f"HTTP {st} {got}")
                continue
            ok, det = (False, got) if st != 200 else expect_decimal(got, want, typed=(ver != "v1"))
            record(f"R6 gremlin-server {ver}: {label}", ok, f"HTTP {st} {det}")


# ---------------------------------------------------------------- main
def main():
    st, b = rest("GET", "/schema/propertykeys/" + P + "balance")
    if st == 200:
        raise SystemExit(f"graph already has a '{P}balance' property key: pick another prefix (argv[4])")
    if not schema():
        raise SystemExit("schema failed")
    indexes()
    olap()
    ids = rest_rw()
    gremlin_checks(ids)
    fails = [r for r in RESULTS if r["status"] == "FAIL"]
    summary = {"tag": TAG, "rest_port": REST, "gremlin_port": GREM,
               "pass": sum(r["status"] == "PASS" for r in RESULTS), "fail": len(fails),
               "na": sum(r["status"] == "N-A" for r in RESULTS), "results": RESULTS}
    print(json.dumps(summary))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
