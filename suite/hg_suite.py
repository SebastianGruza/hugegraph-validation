#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hg_suite.py — jedna suita regresyjna HugeGraph 1.7.0: badany backend (hstore) vs wyrocznia (RocksDB).

Sekcje = terytoria PR-ow (kolejnosc ma znaczenie: J korzysta z danych S i L):
  S  sort keys / ConditionQuery pushdown (#3090, PR #3184)  — krawedzie a->b, REST + gremlin + paging
  L  range index: order / limit / offset / paging (#3140, dom page-500) — 3000 wierzcholkow person
  J  semantyka warunkow LABEL (PR #2994)                    — neq / without / within / konflikt / or / barrier
  K  within x search x range, cache, determinizm, tx niezakomitowana (PR #3182)

Zaden przypadek nie ma zaszytego oczekiwania. Prawda = wynik TEJ SAMEJ suity na wyroczni (ten sam kod
serwera, backend rocksdb). Kazdy przypadek to ZBIOR id: n, uniq, md5 posortowanych id (+ same id w JSON).

  run      hg_suite.py run --port 8081 --load --out oracle.json
           hg_suite.py run --port 8080 --load --out hstore.json --expect oracle.json   (PASS/FAIL inline)
  compare  hg_suite.py compare oracle.json hstore.json [--ids] [--all]

Klasy porownania: OK | DUP (duplikaty tylko u badanego) | MISMATCH (inny zbior) | TARGET-ERR (blad tylko
u badanego = bug backendu) | ORACLE-ERR | BOTH-ERR (ograniczenie serwera/suity) | NEW | MISSING.
"""
import argparse, gzip, hashlib, json, sys, time, urllib.parse, urllib.request
from datetime import datetime

# ────────────────────────────── HTTP / gremlin ──────────────────────────────
class HG:
    def __init__(self, host, port, space, graph):
        self.host, self.port = host, port
        self.root = f"http://{host}:{port}"
        self.base = f"{self.root}/graphspaces/{space}/graphs/{graph}"
        self.grem = f"{self.root}/gremlin"
        self.alias = f"{space}-{graph}"

    @staticmethod
    def _decode(raw):
        if raw[:2] == b"\x1f\x8b":            # serwer gzipuje niezaleznie od Accept-Encoding
            raw = gzip.decompress(raw)
        try:
            return json.loads(raw.decode())
        except Exception:
            return {"exception": raw[:160].decode(errors="replace")}

    def http(self, method, url, body=None, timeout=180):
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, self._decode(r.read())
        except urllib.error.HTTPError as e:
            return e.code, self._decode(e.read())
        except Exception as e:
            return 0, {"exception": str(e)}

    def post(self, path, body): return self.http("POST", self.base + path, body)
    def get(self, path, params): return self.http("GET", self.base + path + "?" + urllib.parse.urlencode(params))

    def gremlin(self, g):
        return self.http("POST", self.grem, {"gremlin": g, "bindings": {}, "language": "gremlin-groovy",
                                             "aliases": {"graph": self.alias, "g": "__g_" + self.alias}})

    def wait_task(self, resp, timeout=180):
        tid = resp.get("task_id") if isinstance(resp, dict) else None
        if not tid: return None
        t0 = time.time()
        while time.time() - t0 < timeout:
            st, d = self.http("GET", f"{self.base}/tasks/{tid}")
            if d.get("task_status") in ("success", "failed", "cancelled"): return d["task_status"]
            time.sleep(0.5)
        return "timeout"

    def info(self):
        st, g = self.http("GET", self.base)
        st, v = self.http("GET", self.root + "/versions")
        return {"backend": g.get("backend") if isinstance(g, dict) else None,
                "version": (v.get("versions") or {}).get("version") if isinstance(v, dict) else None}

    # ── zbiory id: (ids | None, err) ──
    @staticmethod
    def _bad(st, d): return st >= 400 or st == 0 or "exception" in d
    @staticmethod
    def _err(d): return (d.get("message") or str(d))[:110]

    def ids_grem(self, g):
        st, d = self.gremlin(g)
        if self._bad(st, d): return None, self._err(d)
        data = d.get("result", {}).get("data", []) or []
        return [x["id"] if isinstance(x, dict) else x for x in data], None

    def ids_rest(self, path, key, params, limit=None, page=None):
        p = dict(params)
        if limit is not None: p["limit"] = limit
        if page is not None: p["page"] = page
        st, d = self.get(path, p)
        if self._bad(st, d): return None, self._err(d), None
        return [x["id"] for x in d.get(key, [])], None, d.get("page")

    def ids_edges(self, params, limit=5000):
        r, e, _ = self.ids_rest("/graph/edges", "edges", params, limit); return r, e
    def ids_vertices(self, params, limit=10000):
        r, e, _ = self.ids_rest("/graph/vertices", "vertices", params, limit); return r, e

    def paged(self, path, key, params, size, max_pages=5000):
        seen, page, pages = [], "", 0
        while True:
            r, e, nxt = self.ids_rest(path, key, params, limit=size, page=page)
            if r is None: return None, e
            seen += r; pages += 1
            if not nxt or pages > max_pages: break
            page = nxt
        return seen, None


# ────────────────────────────── raport / porownanie ──────────────────────────────
def H(ids): return hashlib.md5("\n".join(sorted(map(str, ids))).encode()).hexdigest()[:12]

def classify(o, t):
    """o = rekord wyroczni (None = brak), t = rekord badany -> (klasa, notatka)."""
    if o is None: return "NEW", ""
    oe, te = "err" in o, "err" in t
    if oe and te: return "BOTH-ERR", ""
    if te: return "TARGET-ERR", f"oracle n={o['n']}"
    if oe: return "ORACLE-ERR", f"target n={t['n']}"
    if (o["n"], o["uniq"], o["h"]) == (t["n"], t["uniq"], t["h"]): return "OK", ""
    if t["n"] != t["uniq"] and o["n"] == o["uniq"]: return "DUP", f"n={t['n']} uniq={t['uniq']} oracle n={o['n']}"
    note = f"oracle n={o['n']} h={o['h']} target n={t['n']} h={t['h']}"
    if "ids" in o and "ids" in t:
        so, st = set(o["ids"]), set(t["ids"])
        note += f" only-oracle={len(so - st)} only-target={len(st - so)}"
    return "MISMATCH", note

CLASSES = ("OK", "DUP", "MISMATCH", "TARGET-ERR", "ORACLE-ERR", "BOTH-ERR", "NEW", "MISSING")

class Report:
    def __init__(self, expect_path=None, keep_ids=True):
        self.cases, self.keys, self.section = [], set(), "?"
        self.keep_ids, self.tally = keep_ids, {}
        self.expect = None
        if expect_path:
            self.expect = {(c["section"], c["name"]): c for c in json.load(open(expect_path))["cases"]}

    def sec(self, code, title):
        self.section = code; print(f"\n=== {code}. {title} ===", flush=True)

    def _name(self, name):
        k, i = (self.section, name), 2
        while k in self.keys: k = (self.section, f"{name} #{i}"); i += 1
        self.keys.add(k); return k[1]

    def _emit(self, rec, line):
        if self.expect is not None:
            cls, note = classify(self.expect.get((rec["section"], rec["name"])), rec)
            self.tally[cls] = self.tally.get(cls, 0) + 1
            line += f"  [{cls}{(' ' + note) if note else ''}]"
        self.cases.append(rec); print(line, flush=True)

    def case(self, name, ids, err=None):
        """zbior id (ids=None -> blad)"""
        name = self._name(name)
        rec = {"section": self.section, "name": name}
        if ids is None:
            rec["err"] = err; self._emit(rec, f"ERR   {self.section} {name:72} {err}")
        else:
            rec.update(n=len(ids), uniq=len(set(ids)), h=H(ids))
            if self.keep_ids and len(ids) <= 20000: rec["ids"] = sorted(map(str, ids))
            self._emit(rec, f"CASE  {self.section} {name:72} n={rec['n']} uniq={rec['uniq']} h={rec['h']}")
        return ids

    def scalar(self, name, value):
        """liczba/flaga jako przypadek (n=uniq=value); tez porownywana z wyrocznia"""
        name = self._name(name)
        rec = {"section": self.section, "name": name, "n": value, "uniq": value, "h": f"v{value}"}
        self._emit(rec, f"CASE  {self.section} {name:72} n={value} uniq={value} h=v{value}")

    def dump(self, path, meta):
        json.dump({"meta": meta, "cases": self.cases}, open(path, "w"), ensure_ascii=False)


def compare(oracle_path, target_path, show_ids=False, show_all=False):
    O, T = json.load(open(oracle_path)), json.load(open(target_path))
    print(f"oracle: {oracle_path}  {O['meta']}")
    print(f"target: {target_path}  {T['meta']}")
    tk = {(c["section"], c["name"]): c for c in T["cases"]}
    ok = {(c["section"], c["name"]) for c in O["cases"]}
    rows = []
    for o in O["cases"]:
        key = (o["section"], o["name"])
        if key not in tk: rows.append(("MISSING", o["section"], o["name"], "brak u badanego", None, None)); continue
        cls, note = classify(o, tk[key]); rows.append((cls, o["section"], o["name"], note, o, tk[key]))
    for t in T["cases"]:
        if (t["section"], t["name"]) not in ok: rows.append(("NEW", t["section"], t["name"], "brak u wyroczni", None, t))
    tot = {c: sum(1 for r in rows if r[0] == c) for c in CLASSES}
    print("=== SUMMARY  " + "  ".join(f"{c}={n}" for c, n in tot.items() if n or c in ("OK", "MISMATCH", "TARGET-ERR")))
    for s in sorted({r[1] for r in rows}):
        sub = [r for r in rows if r[1] == s]
        print(f"  {s}: " + "  ".join(f"{c}={n}" for c in CLASSES if (n := sum(1 for r in sub if r[0] == c))))
    for cls, s, name, note, o, t in rows:
        if cls == "OK" and not show_all: continue
        print(f"  {cls:10} {s} {name[:60]:60} {note}")
        if show_ids and cls == "MISMATCH" and o and t and "ids" in o and "ids" in t:
            so, st = set(o["ids"]), set(t["ids"])
            print(f"             only-oracle: {sorted(so - st)[:5]}\n             only-target: {sorted(st - so)[:5]}")
    bad = sum(n for c, n in tot.items() if c != "OK")
    return bad


# ────────────────────────────── sekcja S: sort keys / pushdown ──────────────────────────────
def load_S(hg):
    print("### S LOAD schema ('amount' FIRST -> najnizszy id -> pierwsza wartosc w kazdym wierszu)")
    for name, dt, card in (("amount", "DOUBLE", "SINGLE"), ("asset", "TEXT", "SINGLE"), ("epoch", "LONG", "SINGLE"),
                           ("note", "TEXT", "SINGLE"), ("tags", "TEXT", "LIST"), ("flags", "INT", "SET"),
                           ("ok", "BOOLEAN", "SINGLE"), ("ratio", "FLOAT", "SINGLE"), ("cnt", "INT", "SINGLE"),
                           ("ts", "DATE", "SINGLE"), ("name", "TEXT", "SINGLE")):
        st, r = hg.post("/schema/propertykeys", {"name": name, "data_type": dt, "cardinality": card}); hg.wait_task(r)
    # cztery strategie id -> sciezka rownosci OWNER_VERTEX
    for vl in ({"name": "node", "id_strategy": "CUSTOMIZE_STRING"}, {"name": "nnode", "id_strategy": "CUSTOMIZE_NUMBER"},
               {"name": "unode", "id_strategy": "CUSTOMIZE_UUID"},
               {"name": "pnode", "id_strategy": "PRIMARY_KEY", "primary_keys": ["name"], "properties": ["name"]}):
        st, r = hg.post("/schema/vertexlabels", dict({"properties": [], "nullable_keys": []}, **vl))
        print(f"  vertexlabel {vl['name']}: {st}")
    EL = {"frequency": "MULTIPLE", "sort_keys": ["asset", "epoch"], "enable_label_index": False,
          "source_label": "node", "target_label": "node"}
    st, r = hg.post("/schema/edgelabels", dict(EL, name="flow",   # id 1
                                              properties=["asset", "epoch", "amount", "note", "tags", "flags", "ok", "ratio", "cnt", "ts"],
                                              nullable_keys=["note", "tags", "flags", "ok", "ratio", "cnt", "ts"]))
    print(f"  edgelabel flow: {st}")
    for i in range(1, 7):   # ids 2..7, zeby flow_p dostal DWUCYFROWE id (10)
        hg.post("/schema/edgelabels", dict(EL, name=f"dummy{i}", properties=["asset", "epoch", "amount"], nullable_keys=[]))
    for name, src in (("flow_n", "nnode"), ("flow_u", "unode"), ("flow_p", "pnode")):   # ids 8, 9, 10
        st, r = hg.post("/schema/edgelabels", dict(EL, name=name, source_label=src, properties=["asset", "epoch", "amount"], nullable_keys=[]))
        print(f"  edgelabel {name}: {st}")

    print("### S LOAD vertices")
    for v in ({"label": "node", "id": "a"}, {"label": "node", "id": "b"}, {"label": "nnode", "id": 7},
              {"label": "unode", "id": "550e8400-e29b-41d4-a716-446655440000"}):
        hg.post("/graph/vertices", dict(v, properties={}))
    st, r = hg.post("/graph/vertices", {"label": "pnode", "properties": {"name": "alice"}})
    pnode_vid = r.get("id") if isinstance(r, dict) and "id" in r else "4:alice"
    print(f"  pnode id: {pnode_vid}")

    print("### S LOAD edges a->b (flow)")
    E = {"label": "flow", "outV": "a", "outVLabel": "node", "inV": "b", "inVLabel": "node"}
    rows = [  # parser: pierwszy bajt 'amount' 1.5->0x3F, 2.5->0x40, 0.0->0x00, -1.0->0xBF
        {"asset": "ETC", "epoch": 100, "amount": 1.5}, {"asset": "ETC", "epoch": 200, "amount": 2.5},
        {"asset": "ETC", "epoch": -5, "amount": 0.0}, {"asset": "ETC", "epoch": 0, "amount": -1.0},
        # granice LongEncoding
        {"asset": "ETC", "epoch": 63, "amount": 63.5}, {"asset": "ETC", "epoch": 64, "amount": 64.5},
        {"asset": "ETC", "epoch": 4095, "amount": 4095.5}, {"asset": "ETC", "epoch": 4096, "amount": 4096.5},
        {"asset": "ETC", "epoch": 9223372036854775807, "amount": 9.5},
        # kazdy opcjonalny typ na jednym wierszu
        {"asset": "ETC", "epoch": 300, "amount": 3.5, "note": "x" * 300, "tags": ["x", "y", "zażółć"], "flags": [1, 2, 3],
         "ok": True, "ratio": 0.25, "cnt": 7, "ts": "2026-09-02 10:00:00"},
        {"asset": "ETC", "epoch": 301, "amount": 3.6, "note": "", "tags": [], "ok": False},
        {"asset": "ETC", "epoch": 302, "amount": 3.7, "note": "zażółć gęślą jaźń 😀"},
        # inne assety: prefix / case / whitespace / separator / non-ASCII
        {"asset": "BTC", "epoch": 100, "amount": 0.1}, {"asset": "ET", "epoch": 100, "amount": 0.2},
        {"asset": "ETCX", "epoch": 100, "amount": 0.3}, {"asset": "etc", "epoch": 100, "amount": 0.4},
        {"asset": "Etc", "epoch": 100, "amount": 0.5}, {"asset": "ETC ", "epoch": 100, "amount": 0.6},
        {"asset": "ETC!X", "epoch": 100, "amount": 0.7}, {"asset": "ÉTC", "epoch": 100, "amount": 0.8},
        {"asset": "～", "epoch": 100, "amount": 0.9}, {"asset": "😀", "epoch": 100, "amount": 1.1},
    ]
    bad = 0
    for p in rows:
        st, r = hg.post("/graph/edges", dict(E, properties=p)); bad += st not in (200, 201)
    print(f"  {len(rows)} single edges, errors={bad}")
    print("### S LOAD 1200 bulk edges ETC epoch 1000..2199 (cnt = epoch % 10)")
    for start in (1000, 1400, 1800):
        batch = [dict(E, properties={"asset": "ETC", "epoch": e, "amount": e + 0.5, "cnt": e % 10}) for e in range(start, start + 400)]
        st, r = hg.post("/graph/edges/batch", batch); print(f"  batch {start}: {st}")
    print("### S LOAD edges from number / uuid / primary-key vertices")
    for ep, am in ((100, 1.5), (200, 2.5)):
        hg.post("/graph/edges", {"label": "flow_n", "outV": 7, "outVLabel": "nnode", "inV": "b", "inVLabel": "node",
                                 "properties": {"asset": "ETC", "epoch": ep, "amount": am}})
        # REST body edge-create nie parsuje typowanych id (UUID) -> gremlin addE
        r, e = hg.ids_grem(f"g.V().hasLabel('unode').addE('flow_u').to(__.V('b')).property('asset','ETC').property('epoch',{ep}L).property('amount',{am}d)")
        print(f"  flow_u epoch={ep} (gremlin addE): {'n=' + str(len(r)) if r is not None else 'ERR ' + e}")
        hg.post("/graph/edges", {"label": "flow_p", "outV": pnode_vid, "outVLabel": "pnode", "inV": "b", "inVLabel": "node",
                                 "properties": {"asset": "ETC", "epoch": ep, "amount": am}})
    time.sleep(2); print("### S LOAD done")


def run_S(hg, rep):
    V = {"vertex_id": '"a"', "direction": "OUT", "label": "flow"}
    VB = {"vertex_id": '"b"', "direction": "IN", "label": "flow"}
    def E(name, base, props=None):
        params = dict(base)
        if props is not None: params["properties"] = json.dumps(props, ensure_ascii=False)
        rep.case("REST " + name, *hg.ids_edges(params))
    def G(name, g): rep.case("GRMLN " + name, *hg.ids_grem(g + ".id()"))
    def P(name, size, base, props):
        rep.case(f"PAGED {name}", *hg.paged("/graph/edges", "edges", dict(base, properties=json.dumps(props, ensure_ascii=False)), size))

    rep.sec("S", "sort keys / pushdown (#3090, PR #3184)")
    print("--- A. baseline paths")
    E("a outE flow, no condition", V)
    E("asset=ETC & epoch=100 (full equality -> IdPrefixQuery)", V, {"asset": "ETC", "epoch": 100})
    E("asset=ETC & epoch=Long.MAX (full equality)", V, {"asset": "ETC", "epoch": 9223372036854775807})
    E("epoch=100 only (not a prefix -> in-memory filter)", V, {"epoch": 100})
    E("cnt=7 only (plain property, in-memory)", V, {"cnt": 7})
    print("--- B. sort-key prefix equality (IdRangeQuery) on strings")
    for a, tag in (("ETC", ""), ("ET", " (must NOT swallow ETC*)"), ("ETCX", ""), ("etc", " (case-sensitive)"), ("Etc", ""),
                   ("ETC ", " (trailing space)"), ("ETC!X", " (separator char inside value)"), ("BTC", ""),
                   ("ÉTC", " (2-byte UTF-8)"), ("～", " (U+FF5E, 3-byte UTF-8, high UTF-16 unit)"),
                   ("😀", " (U+1F600, surrogate pair)"), ("XYZ", " (no such prefix)")):
        E(f"asset='{a}'{tag}", V, {"asset": a})
    print("--- C. ranges on the LONG sort key (LongEncoding boundaries)")
    for name, rng in (("epoch>=150", "P.gte(150)"), ("epoch<0 (negative encoding)", "P.lt(0)"), ("epoch<=0", "P.lte(0)"),
                      ("epoch>=0", "P.gte(0)"), ("63<=epoch<=64 (1/2-digit edge)", "P.between(63,65)"),
                      ("4095<=epoch<=4096 (2/3-digit edge)", "P.between(4095,4097)"), ("epoch>=4096 (4096 + Long.MAX)", "P.gte(4096)"),
                      ("epoch between(100,200) [gte,lt)", "P.between(100,200)"), ("epoch inside(100,200) (gt,lt)", "P.inside(100,200)"),
                      ("1000<=epoch<2000 (bulk, >1 gRPC batch)", "P.between(1000,2000)"), ("3000<=epoch<4000 (empty range)", "P.between(3000,4000)")):
        E(f"asset=ETC & {name}", V, {"asset": "ETC", "epoch": rng})
    print("--- D. ranges on the STRING sort key")
    E("asset>=ETC", V, {"asset": 'P.gte("ETC")'})
    E("asset>=ETC & asset<ETC!", V, {"asset": 'P.between("ETC","ETC!")'})
    E("asset>=ÉTC (UTF-8 byte order)", V, {"asset": 'P.gte("ÉTC")'})
    E("asset>=～ (UTF-8 vs UTF-16 order)", V, {"asset": 'P.gte("～")'})
    # te same zakresy przez gremlin: czy 'expect a number' to ograniczenie REST czy serwera
    G("g.V('a').outE('flow').has('asset',gte('ETC'))", "g.V('a').outE('flow').has('asset',gte('ETC'))")
    G("g.V('a').outE('flow').has('asset',between('ETC','ETC!'))", "g.V('a').outE('flow').has('asset',between('ETC','ETC!'))")
    G("g.V('a').outE('flow').has('asset',gte('ÉTC'))", "g.V('a').outE('flow').has('asset',gte('ÉTC'))")
    G("g.V('a').outE('flow').has('asset',gte('～'))", "g.V('a').outE('flow').has('asset',gte('～'))")
    print("--- E. IN / within lists (prepareConditionQueryList path)")
    E("asset within(ETC,BTC) & epoch=100", V, {"asset": 'P.within("ETC","BTC")', "epoch": 100})
    E("asset within(ET,ETCX) (prefix list)", V, {"asset": 'P.within("ET","ETCX")'})
    print("--- F. sort-key range combined with a plain property")
    E("asset=ETC & epoch>=1000 & cnt=5", V, {"asset": "ETC", "epoch": "P.gte(1000)", "cnt": 5})
    print("--- G. direction IN on b, BOTH via gremlin")
    E("b inE flow asset=ETC", VB, {"asset": "ETC"})
    E("b inE flow asset=ETC & epoch>=150", VB, {"asset": "ETC", "epoch": "P.gte(150)"})
    G("g.V('a').bothE('flow').has('asset','ETC')", "g.V('a').bothE('flow').has('asset','ETC')")
    G("g.V('a').outE('flow').has('asset','ETC').has('epoch',gte(150))", "g.V('a').outE('flow').has('asset','ETC').has('epoch',gte(150))")
    G("g.V('a').outE('flow').has('asset','ETC').has('epoch',between(63,65))", "g.V('a').outE('flow').has('asset','ETC').has('epoch',between(63,65))")
    G("g.V('a').outE('flow').has('asset',within('ET','ETCX'))", "g.V('a').outE('flow').has('asset',within('ET','ETCX'))")
    G("g.V('a').outE('flow').has('asset','ETC').has('epoch',100) (control)", "g.V('a').outE('flow').has('asset','ETC').has('epoch',100)")
    print("--- H. other owner-vertex id types (OWNER_VERTEX equals) and 2-digit label id (SUB_LABEL=10)")
    pv, _ = hg.ids_vertices({"label": "pnode"}, limit=1)
    pnode_vid = pv[0] if pv else "4:alice"
    for vid, lbl, tag in (("7", "flow_n", "nnode 7 (CUSTOMIZE_NUMBER)"),
                          ('U"550e8400-e29b-41d4-a716-446655440000"', "flow_u", "unode (CUSTOMIZE_UUID)"),
                          (f'"{pnode_vid}"', "flow_p", "pnode alice (PRIMARY_KEY, label id 10)")):
        base = {"vertex_id": vid, "direction": "OUT", "label": lbl}
        E(f"{tag} outE {lbl} asset=ETC", base, {"asset": "ETC"})
        E(f"{tag} outE {lbl} asset=ETC & epoch>=150", base, {"asset": "ETC", "epoch": "P.gte(150)"})
    E("b inE (all labels) asset=ETC & 100<=epoch<=200 (multi-label SUB_LABEL 1/8/9/10)",
      {"vertex_id": '"b"', "direction": "IN"}, {"asset": "ETC", "epoch": "P.between(100,201)"})
    print("--- I. paging (position-carrying scan variant of queryByRange)")
    P("asset=ETC, page size 500", 500, V, {"asset": "ETC"})
    P("asset=ETC & epoch>=150, page size 100", 100, V, {"asset": "ETC", "epoch": "P.gte(150)"})
    P("asset=ETC & 1000<=epoch<2000, page size 333", 333, V, {"asset": "ETC", "epoch": "P.between(1000,2000)"})


# ────────────────────────────── sekcja L: range index / paging ──────────────────────────────
def persons(n):
    for i in range(n):
        yield {"label": "person", "id": f"p{i:05d}",
               "properties": {"age": 18 + (i * 7) % 63,                  # 18..80, duzo remisow
                              "score": round(((i * 37) % 1000) / 10.0, 1),
                              "ts": f"{2010 + (i * 13) % 15}-{1 + (i * 3) % 12:02d}-{1 + (i * 5) % 28:02d} 00:00:00",
                              "cnt": i % 10}}

def load_L(hg, n=3000):
    print("### L LOAD schema person (range x3)")
    for pk in ({"name": "age", "data_type": "INT"}, {"name": "score", "data_type": "FLOAT"},
               {"name": "ts", "data_type": "DATE"}, {"name": "cnt", "data_type": "INT"}):
        st, r = hg.post("/schema/propertykeys", dict(pk, cardinality="SINGLE")); hg.wait_task(r)
    st, r = hg.post("/schema/vertexlabels", {"name": "person", "id_strategy": "CUSTOMIZE_STRING",
                                            "properties": ["age", "score", "ts", "cnt"], "nullable_keys": ["cnt"],
                                            "enable_label_index": True}); print("  vertexlabel person:", st)
    for il in ({"name": "personByAge", "index_type": "RANGE_INT", "fields": ["age"]},
               {"name": "personByScore", "index_type": "RANGE_FLOAT", "fields": ["score"]},
               {"name": "personByTs", "index_type": "RANGE_LONG", "fields": ["ts"]}):
        st, r = hg.post("/schema/indexlabels", dict(il, base_type="VERTEX_LABEL", base_value="person"))
        print(f"  indexlabel {il['name']}: {st} task={hg.wait_task(r)}")
    print(f"### L LOAD {n} persons")
    batch = []
    for v in persons(n):
        batch.append(v)
        if len(batch) == 500:
            st, r = hg.post("/graph/vertices/batch", batch); batch = []
            if st not in (200, 201): print("  batch err:", st, str(r)[:120])
    if batch: hg.post("/graph/vertices/batch", batch)
    time.sleep(2); print("### L LOAD done")


def run_L(hg, rep):
    P = {"label": "person"}
    def props(**kw): return dict(P, properties=json.dumps(kw))
    def R(name, pair): rep.case(name, *pair)
    def GV(g): return hg.ids_grem(g)
    def PG(name, params, size): rep.case(name, *hg.paged("/graph/vertices", "vertices", params, size))

    rep.sec("L", "range index: order / limit / offset / paging (#3140, page-500)")
    print("--- L1. full range results")
    R("age>=30 full", hg.ids_vertices(props(age="P.gte(30)")))
    R("age<=25 full", hg.ids_vertices(props(age="P.lte(25)")))
    R("age between(40,50) full", hg.ids_vertices(props(age="P.between(40,50)")))
    R("score>=50.0 full", hg.ids_vertices(props(score="P.gte(50.0)")))
    R("ts>=2018 full", hg.ids_vertices(props(ts='P.gte("2018-01-01 00:00:00")')))
    R("age=32 exact (ties)", hg.ids_vertices(props(age=32)))
    print("--- L2. limited slices (global order across partitions)")
    for N in (1, 2, 10, 100, 1000):
        R(f"age>=30 limit {N} (REST)", hg.ids_vertices(props(age="P.gte(30)"), limit=N))
        R(f"g.V age>=30 limit {N}", GV(f"g.V().hasLabel('person').has('age',gte(30)).limit({N}).id()"))
    R("g.V ts>=2013 limit 2 (#3140 example)", GV("g.V().hasLabel('person').has('ts',gte('2013-01-01 00:00:00')).limit(2).id()"))
    R("g.V score>80 limit 20", GV("g.V().hasLabel('person').has('score',gt(80.0)).limit(20).id()"))
    print("--- L3. offset slices tile the full set (range(a,b))")
    R("age>=30 gremlin full", GV("g.V().hasLabel('person').has('age',gte(30)).id()"))
    tiles = []
    for a in range(0, 2600, 400):
        t, e = GV(f"g.V().hasLabel('person').has('age',gte(30)).range({a},{a + 400}).id()")
        rep.case(f"range({a},{a + 400})", t, e)
        if t is None: tiles = None
        elif tiles is not None: tiles += t
    rep.case("union of range tiles", tiles, "some tile failed" if tiles is None else None)
    rep.case("range(0,10) + range(10,20) (disjoint?)",
             (GV("g.V().hasLabel('person').has('age',gte(30)).range(0,10).id()")[0] or []) +
             (GV("g.V().hasLabel('person').has('age',gte(30)).range(10,20).id()")[0] or []))
    print("--- L4. paging sweep (page cursor; page-500 family)")
    for size in (1, 7, 50, 100, 333, 500, 1000):
        PG(f"age>=30 paged size={size}", props(age="P.gte(30)"), size)
    for size in (100, 500):
        PG(f"score>=50 paged size={size}", props(score="P.gte(50.0)"), size)
        PG(f"ts>=2018 paged size={size}", props(ts='P.gte("2018-01-01 00:00:00")'), size)
    PG("age=32 ties paged size=7", props(age=32), 7)
    print("--- L5. ordered traversals (order().by index key)")
    R("age>=30 order by age,id limit 5 (tie group)", GV("g.V().hasLabel('person').has('age',gte(30)).order().by('age').by(id).limit(5).id()"))
    R("age>=30 order by age,id limit 50", GV("g.V().hasLabel('person').has('age',gte(30)).order().by('age').by(id).limit(50).id()"))
    R("age>=30 order by age desc,id limit 50", GV("g.V().hasLabel('person').has('age',gte(30)).order().by('age',desc).by(id).limit(50).id()"))


# ────────────────────────────── sekcja J: semantyka LABEL ──────────────────────────────
def load_J(hg):
    print("### J LOAD dummy1..3 edges a->b (40 each, ETC/BTC, epoch 10..49)")
    for d in (1, 2, 3):
        batch = [{"label": f"dummy{d}", "outV": "a", "outVLabel": "node", "inV": "b", "inVLabel": "node",
                  "properties": {"asset": "ETC" if i % 2 == 0 else "BTC", "epoch": 10 + i, "amount": d * 100 + i + 0.5}}
                 for i in range(40)]
        st, r = hg.post("/graph/edges/batch", batch); print(f"  dummy{d}: {st}")
    print("### J LOAD robot vertices (300, range index age)")
    st, r = hg.post("/schema/vertexlabels", {"name": "robot", "id_strategy": "CUSTOMIZE_STRING",
                                            "properties": ["age", "score", "cnt"], "nullable_keys": ["cnt"],
                                            "enable_label_index": True}); print("  vertexlabel robot:", st)
    st, r = hg.post("/schema/indexlabels", {"name": "robotByAge", "base_type": "VERTEX_LABEL", "base_value": "robot",
                                           "index_type": "RANGE_INT", "fields": ["age"]})
    print("  indexlabel robotByAge:", st, hg.wait_task(r))
    batch = [{"label": "robot", "id": f"r{i:04d}", "properties": {"age": 18 + (i * 11) % 63, "score": (i % 100) / 2.0}}
             for i in range(300)]
    st, r = hg.post("/graph/vertices/batch", batch); print("  robots:", st)
    time.sleep(2); print("### J LOAD done")


def run_J(hg, rep):
    def R(name, g): rep.case(name, *hg.ids_grem(g))
    A_, B_ = "g.V('a').outE()", "g.V('b').inE()"
    rep.sec("J", "semantyka warunkow LABEL (PR #2994)")
    print("--- J1. edges: single / multi label + sort keys")
    R("outE flow asset=ETC", "g.V('a').outE('flow').has('asset','ETC').id()")
    R("outE(flow,dummy1) asset=ETC", "g.V('a').outE('flow','dummy1').has('asset','ETC').id()")
    R("outE within(flow,dummy2) ETC epoch>=150", A_ + ".hasLabel(within('flow','dummy2')).has('asset','ETC').has('epoch',gte(150)).id()")
    R("outE within(dummy1,dummy2,dummy3) epoch between(10,20)", A_ + ".hasLabel(within('dummy1','dummy2','dummy3')).has('epoch',between(10,20)).id()")
    R("outE within(dummy1,dummy3) asset=BTC", A_ + ".hasLabel(within('dummy1','dummy3')).has('asset','BTC').id()")
    print("--- J2. edges: negative labels")
    R("outE neq(flow)", A_ + ".hasLabel(neq('flow')).id()")
    R("outE without(flow,dummy1)", A_ + ".hasLabel(without('flow','dummy1')).id()")
    R("outE neq(flow) asset=ETC", A_ + ".hasLabel(neq('flow')).has('asset','ETC').id()")
    R("outE asset=ETC then neq(dummy3)", A_ + ".has('asset','ETC').hasLabel(neq('dummy3')).id()")
    R("outE neq(flow) neq(dummy1) (double negative)", A_ + ".hasLabel(neq('flow')).hasLabel(neq('dummy1')).id()")
    R("outE without(flow) epoch>=30", A_ + ".hasLabel(without('flow')).has('epoch',gte(30)).id()")
    print("--- J3. edges: conflicting labels")
    R("outE flow AND dummy1 (conflict -> 0)", A_ + ".hasLabel('flow').hasLabel('dummy1').id()")
    R("outE flow AND within(dummy1,dummy2) (conflict)", A_ + ".hasLabel('flow').hasLabel(within('dummy1','dummy2')).id()")
    R("outE flow AND dummy1 + asset=ETC", A_ + ".hasLabel('flow').hasLabel('dummy1').has('asset','ETC').id()")
    R("outE within(flow,dummy1) AND within(dummy1,dummy2) (=dummy1)", A_ + ".hasLabel(within('flow','dummy1')).hasLabel(within('dummy1','dummy2')).id()")
    print("--- J4. edges: mixed connectives")
    R("outE or(dummy1, epoch=100)", A_ + ".or(hasLabel('dummy1'), has('epoch',100)).id()")
    R("outE or(neq(flow), asset=BTC)", A_ + ".or(hasLabel(neq('flow')), has('asset','BTC')).id()")
    R("outE and(neq(flow), asset=ETC)", A_ + ".and(hasLabel(neq('flow')), has('asset','ETC')).id()")
    R("outE not(hasLabel(flow))", A_ + ".not(hasLabel('flow')).id()")
    print("--- J5. edges: negative label across barrier / sideEffect")
    R("outE neq(flow) barrier asset=ETC", A_ + ".hasLabel(neq('flow')).barrier().has('asset','ETC').id()")
    R("outE neq(flow) sideEffect asset=ETC", A_ + ".hasLabel(neq('flow')).sideEffect(identity()).has('asset','ETC').id()")
    R("outE asset=ETC barrier neq(flow)", A_ + ".has('asset','ETC').barrier().hasLabel(neq('flow')).id()")
    print("--- J6. incoming edges on b (multi-label sources)")
    R("inE neq(flow) asset=ETC", B_ + ".hasLabel(neq('flow')).has('asset','ETC').id()")
    R("inE within(flow_n,flow_p) ETC epoch>=150", B_ + ".hasLabel(within('flow_n','flow_p')).has('asset','ETC').has('epoch',gte(150)).id()")
    R("inE without(flow,dummy1,dummy2,dummy3)", B_ + ".hasLabel(without('flow','dummy1','dummy2','dummy3')).id()")
    R("inE all labels epoch=100", B_ + ".has('epoch',100).id()")
    print("--- J7. vertices: labels + range index")
    R("V neq(person) age>=30", "g.V().hasLabel(neq('person')).has('age',gte(30)).id()")
    R("V age>=30 neq(robot)", "g.V().has('age',gte(30)).hasLabel(neq('robot')).id()")
    R("V within(person,robot) age between(30,40)", "g.V().hasLabel(within('person','robot')).has('age',between(30,40)).id()")
    R("V person AND robot (conflict)", "g.V().hasLabel('person').hasLabel('robot').id()")
    R("V or(robot, age=32)", "g.V().or(hasLabel('robot'), has('age',32)).id()")
    R("V neq(person) neq(robot) age>=30 (double negative)", "g.V().hasLabel(neq('person')).hasLabel(neq('robot')).has('age',gte(30)).id()")
    R("V age>=30 without(person,robot)", "g.V().has('age',gte(30)).hasLabel(without('person','robot')).id()")
    R("V without(person) age>=60 barrier", "g.V().hasLabel(without('person')).barrier().has('age',gte(60)).id()")
    R("V age>=60 neq(person) limit 20 order id", "g.V().has('age',gte(60)).hasLabel(neq('person')).order().by(id).limit(20).id()")


# ────────────────────────────── sekcja K: within x search x range ──────────────────────────────
NAMES = ["北京诚信科技", "上海诚信贸易", "gold trading ltd", "silver gold mining", "诚信 gold partners",
         "plain company", "深圳信诚有限", "warsaw gold exchange", "诚信", "no match here"]

def load_K(hg):
    print("### K LOAD schema firm/deal (range x2 + search)")
    for pk in ({"name": "type", "data_type": "INT"}, {"name": "confirmType", "data_type": "INT"},
               {"name": "fname", "data_type": "TEXT"}, {"name": "kind", "data_type": "INT"},
               {"name": "amt", "data_type": "INT"}, {"name": "note", "data_type": "TEXT"}):
        st, r = hg.post("/schema/propertykeys", dict(pk, cardinality="SINGLE")); hg.wait_task(r)
    st, r = hg.post("/schema/vertexlabels", {"name": "firm", "id_strategy": "CUSTOMIZE_STRING",
                                            "properties": ["type", "confirmType", "fname"], "nullable_keys": [],
                                            "enable_label_index": True}); print("  vertexlabel firm:", st)
    for il in ({"name": "firmByType", "index_type": "RANGE_INT", "fields": ["type"]},
               {"name": "firmByConfirm", "index_type": "RANGE_INT", "fields": ["confirmType"]},
               {"name": "firmBySearch", "index_type": "SEARCH", "fields": ["fname"]}):
        st, r = hg.post("/schema/indexlabels", dict(il, base_type="VERTEX_LABEL", base_value="firm"))
        print(f"  indexlabel {il['name']}: {st} {hg.wait_task(r)}")
    st, r = hg.post("/schema/edgelabels", {"name": "deal", "source_label": "firm", "target_label": "firm",
                                          "frequency": "MULTIPLE", "sort_keys": ["kind"],
                                          "properties": ["kind", "amt", "note"], "nullable_keys": [],
                                          "enable_label_index": True}); print("  edgelabel deal:", st)
    for il in ({"name": "dealByAmt", "index_type": "RANGE_INT", "fields": ["amt"]},
               {"name": "dealBySearch", "index_type": "SEARCH", "fields": ["note"]}):
        st, r = hg.post("/schema/indexlabels", dict(il, base_type="EDGE_LABEL", base_value="deal"))
        print(f"  indexlabel {il['name']}: {st} {hg.wait_task(r)}")
    print("### K LOAD 200 firms + 100 deals from f0000")
    firms = [{"label": "firm", "id": f"f{i:04d}", "properties": {"type": 1 + i % 5, "confirmType": 1 + i % 4,
                                                                "fname": NAMES[i % 10] + f" {i}"}} for i in range(200)]
    st, r = hg.post("/graph/vertices/batch", firms); print("  firms:", st)
    deals = [{"label": "deal", "outV": "f0000", "outVLabel": "firm", "inV": f"f{1 + i % 199:04d}", "inVLabel": "firm",
              "properties": {"kind": 1 + i % 3, "amt": i, "note": NAMES[(i * 3) % 10] + f" d{i}"}} for i in range(100)]
    st, r = hg.post("/graph/edges/batch", deals); print("  deals:", st)
    time.sleep(2); print("### K LOAD done")


def run_K(hg, rep):
    def R(name, g):
        """kazde zapytanie 2x: drugi przebieg = cache hit (#3182 omija cache przy post-filtrze)"""
        r = rep.case(name, *hg.ids_grem(g))
        rep.case(name + " [2nd run/cache]", *hg.ids_grem(g))
        return r
    F, CN = "g.V().hasLabel('firm')", "诚信"
    COMBO_GOLD = F + ".has('type',gte(2)).has('confirmType',within(1,2,3)).has('fname',Text.contains('gold')).id()"
    rep.sec("K", "within x search x range, cache, determinizm, tx niezakomitowana (PR #3182)")
    print("--- K1. the #3180 combo: range + within(range) + search")
    combo = F + f".has('type',gte(2)).has('confirmType',within(1,2,3)).has('fname',Text.contains('{CN}')).id()"
    R("type>=2 & confirm within(1,2,3) & contains(诚信)", combo)
    R("type>=2 & confirm within(1,2,3) & contains(gold)", COMBO_GOLD)
    R("within(2) single", F + ".has('type',gte(2)).has('confirmType',within(2)).has('fname',Text.contains('gold')).id()")
    R("within(1,2,3,4) all", F + ".has('type',gte(2)).has('confirmType',within(1,2,3,4)).has('fname',Text.contains('gold')).id()")
    R("within(9) absent", F + ".has('type',gte(2)).has('confirmType',within(9)).has('fname',Text.contains('gold')).id()")
    R("type within(1,3,5) + search", F + ".has('type',within(1,3,5)).has('fname',Text.contains('gold')).id()")
    print("--- K2. components alone")
    R("search only contains(诚信)", F + f".has('fname',Text.contains('{CN}')).id()")
    R("search only contains(gold)", F + ".has('fname',Text.contains('gold')).id()")
    R("range+within no search", F + ".has('type',gte(2)).has('confirmType',within(1,2,3)).id()")
    R("search+within no range", F + ".has('confirmType',within(1,3)).has('fname',Text.contains('gold')).id()")
    R("two ranges", F + ".has('type',between(2,4)).has('confirmType',gte(3)).id()")
    R("search + range + limit 10 order id", F + ".has('type',gte(2)).has('fname',Text.contains('gold')).order().by(id).limit(10).id()")
    print("--- K3. determinism: combo x10")
    hs = set()
    for _ in range(10):
        r, e = hg.ids_grem(combo); hs.add(H(r) if r is not None else "ERR")
    rep.scalar("combo x10 distinct-hashes (1 = stable)", len(hs))
    print("--- K4. edges: within(sort key) x search x range")
    E = "g.V('f0000').outE('deal')"
    R("deal kind within(1,2) amt>=10 contains(gold)", E + ".has('kind',within(1,2)).has('amt',gte(10)).has('note',Text.contains('gold')).id()")
    R("deal kind within(1,2,3) contains(诚信)", E + f".has('kind',within(1,2,3)).has('note',Text.contains('{CN}')).id()")
    R("deal amt between(20,60) kind within(3)", E + ".has('amt',between(20,60)).has('kind',within(3)).id()")
    R("deal search only", E + ".has('note',Text.contains('gold')).id()")
    print("--- K5. uncommitted-tx path (addV + query before commit, then after)")
    base = rep.case("K5 baseline combo gold", *hg.ids_grem(COMBO_GOLD))
    script = ("graph.addVertex(T.label,'firm',T.id,'fx0001','type',3,'confirmType',2,'fname','诚信 gold fresh 1');"
              "graph.addVertex(T.label,'firm',T.id,'fx0002','type',3,'confirmType',3,'fname','诚信 gold fresh 2');"
              "graph.addVertex(T.label,'firm',T.id,'fx0003','type',1,'confirmType',2,'fname','诚信 gold fresh 3');"
              "before = g.V().hasLabel('firm').has('type',gte(2)).has('confirmType',within(1,2,3)).has('fname',Text.contains('gold')).id().toList();"
              "graph.tx().commit();"
              "after = g.V().hasLabel('firm').has('type',gte(2)).has('confirmType',within(1,2,3)).has('fname',Text.contains('gold')).id().toList();"
              "[before.size(), after.size(), before.sort(), after.sort()]")
    st, d = hg.gremlin(script)
    if HG._bad(st, d):
        rep.case("K5 before-commit set", None, HG._err(d))
    else:
        b, a, bl, al = d.get("result", {}).get("data", [])
        rep.case("K5 before-commit set", bl)
        rep.case("K5 after-commit set", al)
        # swieze elementy widoczne juz PRZED commitem (2 z 3 pasuja), commit nic nie zmienia
        rep.scalar("K5 before-commit minus baseline (2 = fresh visible in tx)", b - (len(base) if base is not None else -1))
        rep.scalar("K5 after minus before (0 = commit changes nothing)", a - b)
    R("K5 combo after fresh commit (baseline+2)", COMBO_GOLD)
    hg.gremlin("g.V('fx0001','fx0002','fx0003').drop()")   # sprzatanie: przebiegi idempotentne


# ────────────────────────────── main ──────────────────────────────
SECTIONS = {"S": ("sort keys / pushdown (#3090, PR #3184)", load_S, run_S),
            "L": ("range index / paging (#3140)", load_L, run_L),
            "J": ("semantyka LABEL (PR #2994)", load_J, run_J),
            "K": ("within x search x range / cache / tx (PR #3182)", load_K, run_K)}

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="odpal suite na jednym serwerze")
    r.add_argument("--host", default="localhost"); r.add_argument("--port", type=int, required=True)
    r.add_argument("--graph", default="hugegraph"); r.add_argument("--space", default="DEFAULT")
    r.add_argument("--load", action="store_true", help="najpierw zbuduj scheme + dane wybranych sekcji (swiezy graf)")
    r.add_argument("--sections", default="S,L,J,K")
    r.add_argument("--out", help="raport JSON (zbiory id)")
    r.add_argument("--expect", help="raport wyroczni: klasyfikuj kazdy przypadek inline")
    r.add_argument("--no-ids", action="store_true", help="w JSON tylko n/uniq/hash")
    r.add_argument("--persons", type=int, default=3000)
    c = sub.add_parser("compare", help="porownaj dwa raporty JSON (wyrocznia vs badany)")
    c.add_argument("oracle"); c.add_argument("target")
    c.add_argument("--ids", action="store_true", help="pokaz po 5 id roznicy przy MISMATCH")
    c.add_argument("--all", action="store_true", help="wypisz tez OK")
    A = ap.parse_args()

    if A.cmd == "compare":
        sys.exit(1 if compare(A.oracle, A.target, A.ids, A.all) else 0)

    secs = [s.strip().upper() for s in A.sections.split(",") if s.strip()]
    bad = [s for s in secs if s not in SECTIONS]
    if bad: sys.exit(f"nieznane sekcje: {bad} (dostepne: {list(SECTIONS)})")
    secs = [s for s in SECTIONS if s in secs]          # kolejnosc S,L,J,K niezaleznie od podanej
    hg = HG(A.host, A.port, A.space, A.graph)
    meta = dict(hg.info(), host=A.host, port=A.port, graph=A.graph, sections=secs, load=A.load,
                started=datetime.now().isoformat(timespec="seconds"))
    print(f"# hg_suite run {meta}")
    if A.load:
        for s in secs:
            SECTIONS[s][1](hg) if s != "L" else load_L(hg, A.persons)
    rep = Report(A.expect, keep_ids=not A.no_ids)
    t0 = time.time()
    for s in secs:
        SECTIONS[s][2](hg, rep)
    meta["seconds"] = round(time.time() - t0, 1); meta["cases"] = len(rep.cases)
    print(f"\n=== DONE {len(rep.cases)} cases in {meta['seconds']}s ===")
    if rep.expect is not None:
        print("=== vs oracle: " + "  ".join(f"{k}={rep.tally.get(k, 0)}" for k in CLASSES if rep.tally.get(k)))
    if A.out:
        rep.dump(A.out, meta); print(f"raport: {A.out}")
    if rep.expect is not None and any(k != "OK" for k in rep.tally):
        sys.exit(1)

if __name__ == "__main__":
    main()
