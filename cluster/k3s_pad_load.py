import json, sys, time, random, string
sys.path.insert(0, "/home/seba")
import k3s_faults as F
rest = F.Rest()
for body, path in [({"name":"blob","data_type":"TEXT","cardinality":"SINGLE"}, "/schema/propertykeys"),
                   ({"name":"pad","id_strategy":"PRIMARY_KEY","primary_keys":["name"],"properties":["name","blob"],"enable_label_index":False}, "/schema/vertexlabels")]:
    st, b, _ = rest.post(path, body); print(path, st, b[:80], flush=True)
N = int(sys.argv[1]); PER = 1000; rnd = random.Random(7)
blob = "".join(rnd.choice(string.ascii_letters) for _ in range(1024))
t0 = time.time(); fails = 0
for i in range(0, N, PER):
    vs = [{"label":"pad","properties":{"name":f"pad{j+1000000:08d}","blob":blob[j % 50:] + blob[:j % 50]}} for j in range(i, i + PER)]
    st, b, dt = rest.post("/graph/vertices/batch", vs, timeout=180)
    if st != 201: fails += 1; print("fail", i, st, b[:120], flush=True)
    if (i // PER) % 100 == 0: print(f"{i} vertices, {time.time()-t0:.0f}s, fails {fails}", flush=True)
print(f"done {N} in {time.time()-t0:.0f}s, fails {fails}", flush=True)
