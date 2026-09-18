#!/usr/bin/env python3
"""write | read : schema + data on a HugeGraph REST endpoint. Usage: hg_data.py write|read BASE OUTFILE"""
import json, sys, urllib.request, gzip, time
mode, base, out = sys.argv[1], sys.argv[2].rstrip('/'), sys.argv[3]
G = base + "/graphs/hugegraph"
def call(m, path, body=None, root=False):
    url = (base if root else G) + path
    d = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=d, method=m, headers={"Content-Type": "application/json", "Accept-Encoding": "identity"})
    try:
        r = urllib.request.urlopen(req, timeout=120); raw = r.read(); st = r.status
    except urllib.error.HTTPError as e:
        raw = e.read(); st = e.code
    except Exception as e:
        return 0, {"exception": str(e)}
    if raw[:2] == b"\x1f\x8b": raw = gzip.decompress(raw)
    try: return st, json.loads(raw.decode() or "null")
    except Exception: return st, raw.decode(errors="replace")[:300]
def gremlin(q):
    st, d = call("POST", "/gremlin", {"gremlin": q, "bindings": {}, "language": "gremlin-groovy",
                                       "aliases": {"graph": "DEFAULT-hugegraph", "g": "__g_DEFAULT-hugegraph"}}, root=True)
    if st == 200 and isinstance(d, dict): return st, d.get("result", {}).get("data")
    return st, d
log = []
def rec(label, st, d):
    log.append({"step": label, "status": st, "data": d}); print(f"{st:>3} {label}: {str(d)[:140]}", flush=True)
if mode == "write":
    pks = [("name","TEXT","SINGLE"),("age","INT","SINGLE"),("city","TEXT","SINGLE"),("score","DOUBLE","SINGLE"),
           ("ratio","FLOAT","SINGLE"),("big","LONG","SINGLE"),("ok","BOOLEAN","SINGLE"),("born","DATE","SINGLE"),
           ("tags","TEXT","LIST"),("flags","INT","SET"),("since","INT","SINGLE"),("desc","TEXT","SINGLE"),("weight","DOUBLE","SINGLE")]
    for n,t,c in pks: rec("pk "+n, *call("POST","/schema/propertykeys",{"name":n,"data_type":t,"cardinality":c}))
    rec("vl person", *call("POST","/schema/vertexlabels",{"name":"person","id_strategy":"PRIMARY_KEY","primary_keys":["name"],
        "properties":["name","age","city","score","ratio","big","ok","born","tags","flags"],"nullable_keys":["city","score","ratio","big","ok","born","tags","flags"],"enable_label_index":True}))
    rec("vl software", *call("POST","/schema/vertexlabels",{"name":"software","id_strategy":"AUTOMATIC","properties":["name","desc"],"nullable_keys":["desc"],"enable_label_index":True}))
    rec("vl cust", *call("POST","/schema/vertexlabels",{"name":"cust","id_strategy":"CUSTOMIZE_STRING","properties":["name"],"enable_label_index":True}))
    rec("el knows", *call("POST","/schema/edgelabels",{"name":"knows","source_label":"person","target_label":"person","frequency":"MULTIPLE","sort_keys":["since"],"properties":["since","weight"],"nullable_keys":["weight"],"enable_label_index":True}))
    rec("el created", *call("POST","/schema/edgelabels",{"name":"created","source_label":"person","target_label":"software","frequency":"SINGLE","properties":["weight"],"nullable_keys":["weight"],"enable_label_index":True}))
    rec("el owns", *call("POST","/schema/edgelabels",{"name":"owns","source_label":"cust","target_label":"software","frequency":"SINGLE","enable_label_index":True}))
    rec("il personByCity", *call("POST","/schema/indexlabels",{"name":"personByCity","base_type":"VERTEX_LABEL","base_value":"person","index_type":"SECONDARY","fields":["city"]}))
    rec("il personByAge", *call("POST","/schema/indexlabels",{"name":"personByAge","base_type":"VERTEX_LABEL","base_value":"person","index_type":"RANGE","fields":["age"]}))
    rec("il softwareByDesc", *call("POST","/schema/indexlabels",{"name":"softwareByDesc","base_type":"VERTEX_LABEL","base_value":"software","index_type":"SEARCH","fields":["desc"]}))
    rec("il knowsByWeight", *call("POST","/schema/indexlabels",{"name":"knowsByWeight","base_type":"EDGE_LABEL","base_value":"knows","index_type":"RANGE","fields":["weight"]}))
    time.sleep(3)
    vs = []
    for i in range(1, 21):
        vs.append({"label":"person","properties":{"name":f"p{i}","age":20+i,"city":"Krakow" if i%2 else "Wroclaw","score":i*1.5,"ratio":0.25*i,
                   "big":10**12+i,"ok":i%3==0,"born":f"199{i%10}-01-0{1+i%9}","tags":[f"t{i}",f"u{i}"],"flags":[i,i+100]}})
    for i in range(1, 6): vs.append({"label":"software","properties":{"name":f"s{i}","desc":f"graph database tool number {i}"}})
    for i in range(1, 4): vs.append({"label":"cust","id":f"c{i}","properties":{"name":f"cust{i}"}})
    st, d = call("POST","/graph/vertices/batch", vs); rec("vertices batch", st, d if st!=200 else f"{len(d)} ids")
    ids = d if st // 100 == 2 else []
    def vid(label, name):
        st, d = call("GET", f"/graph/vertices?label={label}&properties=" + urllib.parse.quote(json.dumps({"name": name})))
        return d["vertices"][0]["id"] if st == 200 and d.get("vertices") else None
    import urllib.parse
    P = {f"p{i}": vid("person", f"p{i}") for i in range(1, 21)}
    S = {f"s{i}": ids[19 + i] for i in range(1, 6)}
    es = []
    for i in range(1, 21):
        for k in (1, 2):
            j = (i + k) % 20 + 1
            es.append({"label":"knows","outV":P[f"p{i}"],"inV":P[f"p{j}"],"outVLabel":"person","inVLabel":"person","properties":{"since":2000+k,"weight":i+k*0.5}})
        es.append({"label":"created","outV":P[f"p{i}"],"inV":S[f"s{1+i%5}"],"outVLabel":"person","inVLabel":"software","properties":{"weight":i/10}})
    for i in range(1, 4): es.append({"label":"owns","outV":f"c{i}","inV":S[f"s{i}"],"outVLabel":"cust","inVLabel":"software","properties":{}})
    st, d = call("POST","/graph/edges/batch", es); rec("edges batch", st, d if st!=200 else f"{len(d)} ids")
    rec("gs create test2", *call("POST","/graphs/hugegraph/graph/vertices",{"label":"cust","id":"c99","properties":{"name":"late"}}, root=True))
time.sleep(2)
# reads (both modes)
for path, label in [("/schema/propertykeys","propertykeys"),("/schema/vertexlabels","vertexlabels"),("/schema/edgelabels","edgelabels"),("/schema/indexlabels","indexlabels")]:
    st, d = call("GET", path)
    items = d.get(label, []) if isinstance(d, dict) else d
    rec("schema "+label, st, sorted([x["name"] for x in items]) if isinstance(items, list) else items)
    log[-1]["full"] = items
rec("graphs list", *call("GET", "/graphs", root=True))
rec("graphspaces", *call("GET", "/graphspaces", root=True))
for q in ["g.V().count()","g.E().count()","g.V().hasLabel('person').count()","g.V().hasLabel('software').count()","g.V().hasLabel('cust').count()",
          "g.E().hasLabel('knows').count()","g.E().hasLabel('created').count()","g.E().hasLabel('owns').count()",
          "g.V().has('person','name','p7').valueMap(true)","g.V().has('person','city','Krakow').count()","g.V().has('person','age',gt(30)).count()",
          "g.V().has('software','desc',Text.contains('tool')).count()","g.E().has('knows','weight',gt(10.0)).count()",
          "g.V().has('person','name','p7').outE('knows').has('since',2001).inV().values('name')","g.V('c1').out('owns').values('name')",
          "g.V().has('person','name','p3').values('tags','flags','born','big','ok','ratio','score')"]:
    rec("gremlin "+q, *gremlin(q))
rec("rest vertices", *call("GET","/graph/vertices?limit=3"))
rec("rest edges", *call("GET","/graph/edges?limit=3"))
json.dump(log, open(out, "w"), indent=1, default=str)
