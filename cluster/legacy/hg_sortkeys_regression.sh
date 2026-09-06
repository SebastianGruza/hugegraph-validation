#!/usr/bin/env bash
# Edge-case / regression suite for the hugegraph-struct Condition.compare()/equals() patch
# (Comparable fallback in compare(), asString()/toString() fallback in equals()) on HugeGraph 1.7.0 + HStore.
#
# Every query has an EXPECTED edge count (what a non-pushdown backend returns). The script prints
# PASS / FAIL (wrong count = wrong answer) / ERR (exception) and a summary. Run it on the unpatched
# cluster (range/prefix cases → ERR) and on the patched one (everything → PASS, or you found a regression).
#
# What it stresses:
#   compare():  string sort values (prefix vs exact, case, trailing space, '!' inside a value, non-ASCII where
#               UTF-16 char order != UTF-8 byte order), LongEncoding boundaries (negative, 0, 63/64, 4095/4096,
#               Long.MAX), between/inside/within, direction IN/BOTH, paging, >1 gRPC batch (1200 rows).
#   equals():   LABEL/SUB_LABEL with 1- and 2-digit ids, OWNER_VERTEX with string / number / uuid / primary-key ids.
#   parser:     first property is DOUBLE with first bytes 0x3F, 0x40, 0x00, 0xBF; plus TEXT (empty, long, non-ASCII),
#               LIST<TEXT>, SET<INT>, BOOLEAN, FLOAT, INT, DATE, and nullable properties absent from most rows.
#
# Usage:  LOAD=1 ./hg_sortkeys_regression.sh    (fresh graph: load schema + data, then run)
#         ./hg_sortkeys_regression.sh           (queries only)
set -uo pipefail

HG_HOST=${HG_HOST:-192.168.80.235}
HG_PORT=${HG_PORT:-8080}
HG_GRAPHSPACE=${HG_GRAPHSPACE:-DEFAULT}
HG_GRAPH=${HG_GRAPH:-sg_regress}
LOAD=${LOAD:-0}
BASE="http://${HG_HOST}:${HG_PORT}/graphspaces/${HG_GRAPHSPACE}/graphs/${HG_GRAPH}"
GREMLIN_URL="http://${HG_HOST}:${HG_PORT}/gremlin"
ALIAS="${HG_GRAPHSPACE}-${HG_GRAPH}"
CURL=(curl -sS --compressed --max-time 120)

PASS=0; FAIL=0; ERR=0

post() { "${CURL[@]}" -X POST -H 'Content-Type: application/json' "${BASE}$1" -d "$2"; printf '\n'; }

count_edges() { grep -o '"type":"edge"' | wc -l | tr -d ' '; }

# expect <expected_count> <name> <curl -G args...>
expect() {
  local exp=$1 name=$2; shift 2
  local out; out=$("${CURL[@]}" -G "${BASE}/graph/edges" --data-urlencode 'limit=5000' "$@" 2>&1)
  judge "$exp" "REST  $name" "$out"
}

# gexpect <expected_count> <name> <gremlin>
gexpect() {
  local exp=$1 name=$2 g=$3
  local body; body=$(python3 -c 'import json,sys;print(json.dumps({"gremlin":sys.argv[1],"bindings":{},"language":"gremlin-groovy","aliases":{"graph":sys.argv[2],"g":"__g_"+sys.argv[2]}}))' "$g" "$ALIAS")
  local out; out=$("${CURL[@]}" -X POST -H 'Content-Type: application/json' "$GREMLIN_URL" -d "$body" 2>&1)
  judge "$exp" "GRMLN $name" "$out"
}

# pexpect <expected_total> <name> <page_size> <curl -G args...>   — follows page tokens to the end
pexpect() {
  local exp=$1 name=$2 psize=$3; shift 3
  local out; out=$(python3 - "$BASE" "$psize" "$@" <<'EOF'
import sys, json, subprocess
base, psize, args = sys.argv[1], sys.argv[2], sys.argv[3:]
page, total, pages = "", 0, 0
while True:
    cmd = ["curl","-sS","--compressed","--max-time","120","-G",base+"/graph/edges",
           "--data-urlencode","limit="+psize,"--data-urlencode","page="+page] + args
    raw = subprocess.run(cmd, capture_output=True, text=True).stdout
    try: d = json.loads(raw)
    except Exception: print(raw); sys.exit(0)
    if "exception" in d: print(raw); sys.exit(0)
    total += len(d.get("edges", [])); pages += 1
    page = d.get("page")
    if not page or pages > 1000: break
print('"type":"edge"' * total + "  pages=%d" % pages)
EOF
)
  judge "$exp" "PAGED $name" "$out"
}

judge() {
  local exp=$1 name=$2 out=$3 got
  if grep -q '"exception"' <<<"$out"; then
    ERR=$((ERR+1)); printf 'ERR   %-70s exp=%-5s %s\n' "$name" "$exp" "$(grep -o 'scanning data:[^,]*\|"message":"[^"]\{0,90\}' <<<"$out" | head -1)"
  else
    got=$(count_edges <<<"$out")
    if [[ "$got" == "$exp" ]]; then PASS=$((PASS+1)); printf 'PASS  %-70s got=%s\n' "$name" "$got"
    else FAIL=$((FAIL+1)); printf 'FAIL  %-70s exp=%-5s got=%s\n' "$name" "$exp" "$got"; fi
  fi
}

V=(--data-urlencode 'vertex_id="a"' --data-urlencode 'direction=OUT' --data-urlencode 'label=flow')
VB=(--data-urlencode 'vertex_id="b"' --data-urlencode 'direction=IN'  --data-urlencode 'label=flow')

# ═══════════════════════════════════════════════════════════════════════════════
if [[ "$LOAD" == "1" ]]; then
echo "### LOAD schema"
# property keys — 'amount' FIRST (lowest id → first value in every row → hits the header-byte bug)
post /schema/propertykeys '{"name":"amount","data_type":"DOUBLE","cardinality":"SINGLE"}'
post /schema/propertykeys '{"name":"asset","data_type":"TEXT","cardinality":"SINGLE"}'
post /schema/propertykeys '{"name":"epoch","data_type":"LONG","cardinality":"SINGLE"}'
post /schema/propertykeys '{"name":"note","data_type":"TEXT","cardinality":"SINGLE"}'
post /schema/propertykeys '{"name":"tags","data_type":"TEXT","cardinality":"LIST"}'
post /schema/propertykeys '{"name":"flags","data_type":"INT","cardinality":"SET"}'
post /schema/propertykeys '{"name":"ok","data_type":"BOOLEAN","cardinality":"SINGLE"}'
post /schema/propertykeys '{"name":"ratio","data_type":"FLOAT","cardinality":"SINGLE"}'
post /schema/propertykeys '{"name":"cnt","data_type":"INT","cardinality":"SINGLE"}'
post /schema/propertykeys '{"name":"ts","data_type":"DATE","cardinality":"SINGLE"}'
post /schema/propertykeys '{"name":"name","data_type":"TEXT","cardinality":"SINGLE"}'

# vertex labels: ids 1..4 — four different id strategies for the OWNER_VERTEX equality path
post /schema/vertexlabels '{"name":"node","id_strategy":"CUSTOMIZE_STRING","properties":[],"nullable_keys":[]}'
post /schema/vertexlabels '{"name":"nnode","id_strategy":"CUSTOMIZE_NUMBER","properties":[],"nullable_keys":[]}'
post /schema/vertexlabels '{"name":"unode","id_strategy":"CUSTOMIZE_UUID","properties":[],"nullable_keys":[]}'
post /schema/vertexlabels '{"name":"pnode","id_strategy":"PRIMARY_KEY","primary_keys":["name"],"properties":["name"],"nullable_keys":[]}'

EL='"frequency":"MULTIPLE","sort_keys":["asset","epoch"],"enable_label_index":false'
# flow = edge label id 1
post /schema/edgelabels '{"name":"flow","source_label":"node","target_label":"node",'"$EL"',
  "properties":["asset","epoch","amount","note","tags","flags","ok","ratio","cnt","ts"],
  "nullable_keys":["note","tags","flags","ok","ratio","cnt","ts"]}'
# six dummies (ids 2..7) so that flow_p gets a TWO-DIGIT label id (10) — exercises LongId(10) vs "1" equality
for i in 1 2 3 4 5 6; do
  post /schema/edgelabels '{"name":"dummy'"$i"'","source_label":"node","target_label":"node",'"$EL"',"properties":["asset","epoch","amount"],"nullable_keys":[]}'
done
post /schema/edgelabels '{"name":"flow_n","source_label":"nnode","target_label":"node",'"$EL"',"properties":["asset","epoch","amount"],"nullable_keys":[]}'   # id 8
post /schema/edgelabels '{"name":"flow_u","source_label":"unode","target_label":"node",'"$EL"',"properties":["asset","epoch","amount"],"nullable_keys":[]}'   # id 9
post /schema/edgelabels '{"name":"flow_p","source_label":"pnode","target_label":"node",'"$EL"',"properties":["asset","epoch","amount"],"nullable_keys":[]}'   # id 10

echo "### LOAD vertices"
post /graph/vertices '{"label":"node","id":"a","properties":{}}'
post /graph/vertices '{"label":"node","id":"b","properties":{}}'
post /graph/vertices '{"label":"nnode","id":7,"properties":{}}'
post /graph/vertices '{"label":"unode","id":"550e8400-e29b-41d4-a716-446655440000","properties":{}}'
post /graph/vertices '{"label":"pnode","properties":{"name":"alice"}}'        # id should be "4:alice"

echo "### LOAD edges a->b (label flow)"
E='"label":"flow","outV":"a","outVLabel":"node","inV":"b","inVLabel":"node"'
# parser stress via first byte of 'amount': 1.5→0x3F, 2.5→0x40, 0.0→0x00, -1.0→0xBF
post /graph/edges '{'"$E"',"properties":{"asset":"ETC","epoch":100,"amount":1.5}}'
post /graph/edges '{'"$E"',"properties":{"asset":"ETC","epoch":200,"amount":2.5}}'
post /graph/edges '{'"$E"',"properties":{"asset":"ETC","epoch":-5,"amount":0.0}}'
post /graph/edges '{'"$E"',"properties":{"asset":"ETC","epoch":0,"amount":-1.0}}'
# LongEncoding length boundaries
post /graph/edges '{'"$E"',"properties":{"asset":"ETC","epoch":63,"amount":63.5}}'
post /graph/edges '{'"$E"',"properties":{"asset":"ETC","epoch":64,"amount":64.5}}'
post /graph/edges '{'"$E"',"properties":{"asset":"ETC","epoch":4095,"amount":4095.5}}'
post /graph/edges '{'"$E"',"properties":{"asset":"ETC","epoch":4096,"amount":4096.5}}'
post /graph/edges '{'"$E"',"properties":{"asset":"ETC","epoch":9223372036854775807,"amount":9.5}}'
# every optional property type on one row (TEXT long / LIST / SET / BOOLEAN / FLOAT / INT / DATE)
LONGNOTE=$(printf 'x%.0s' $(seq 1 300))
post /graph/edges '{'"$E"',"properties":{"asset":"ETC","epoch":300,"amount":3.5,"note":"'"$LONGNOTE"'","tags":["x","y","zażółć"],"flags":[1,2,3],"ok":true,"ratio":0.25,"cnt":7,"ts":"2026-09-02 10:00:00"}}'
post /graph/edges '{'"$E"',"properties":{"asset":"ETC","epoch":301,"amount":3.6,"note":"","tags":[],"ok":false}}'
post /graph/edges '{'"$E"',"properties":{"asset":"ETC","epoch":302,"amount":3.7,"note":"zażółć gęślą jaźń 😀"}}'
# other assets: prefix / case / whitespace / separator / non-ASCII
post /graph/edges '{'"$E"',"properties":{"asset":"BTC","epoch":100,"amount":0.1}}'
post /graph/edges '{'"$E"',"properties":{"asset":"ET","epoch":100,"amount":0.2}}'
post /graph/edges '{'"$E"',"properties":{"asset":"ETCX","epoch":100,"amount":0.3}}'
post /graph/edges '{'"$E"',"properties":{"asset":"etc","epoch":100,"amount":0.4}}'
post /graph/edges '{'"$E"',"properties":{"asset":"Etc","epoch":100,"amount":0.5}}'
post /graph/edges '{'"$E"',"properties":{"asset":"ETC ","epoch":100,"amount":0.6}}'
post /graph/edges '{'"$E"',"properties":{"asset":"ETC!X","epoch":100,"amount":0.7}}'
post /graph/edges '{'"$E"',"properties":{"asset":"ÉTC","epoch":100,"amount":0.8}}'
post /graph/edges '{'"$E"',"properties":{"asset":"～","epoch":100,"amount":0.9}}'
post /graph/edges '{'"$E"',"properties":{"asset":"😀","epoch":100,"amount":1.1}}'

echo "### LOAD 1200 bulk edges a->b ETC epoch 1000..2199 (cnt = epoch % 10)"
for start in 1000 1400 1800; do
  python3 -c '
import json,sys
s=int(sys.argv[1])
print(json.dumps([{"label":"flow","outV":"a","outVLabel":"node","inV":"b","inVLabel":"node",
  "properties":{"asset":"ETC","epoch":e,"amount":e+0.5,"cnt":e%10}} for e in range(s,s+400)]))' "$start" \
  | "${CURL[@]}" -X POST -H 'Content-Type: application/json' "${BASE}/graph/edges/batch" -d @- | head -c 200; echo
done

echo "### LOAD edges from number / uuid / primary-key vertices"
post /graph/edges '{"label":"flow_n","outV":7,"outVLabel":"nnode","inV":"b","inVLabel":"node","properties":{"asset":"ETC","epoch":100,"amount":1.5}}'
post /graph/edges '{"label":"flow_n","outV":7,"outVLabel":"nnode","inV":"b","inVLabel":"node","properties":{"asset":"ETC","epoch":200,"amount":2.5}}'
# UUID: the REST edge-create body does not parse typed ids (EdgeAPI.getVertex) -> gremlin
gexpect 1 "LOAD flow_u epoch=100 (gremlin addE)" "g.V().hasLabel('unode').addE('flow_u').to(__.V('b')).property('asset','ETC').property('epoch',100L).property('amount',1.5d)"
gexpect 1 "LOAD flow_u epoch=200 (gremlin addE)" "g.V().hasLabel('unode').addE('flow_u').to(__.V('b')).property('asset','ETC').property('epoch',200L).property('amount',2.5d)"
post /graph/edges '{"label":"flow_p","outV":"4:alice","outVLabel":"pnode","inV":"b","inVLabel":"node","properties":{"asset":"ETC","epoch":100,"amount":1.5}}'
post /graph/edges '{"label":"flow_p","outV":"4:alice","outVLabel":"pnode","inV":"b","inVLabel":"node","properties":{"asset":"ETC","epoch":200,"amount":2.5}}'
echo "### LOAD done"
fi
# ═══════════════════════════════════════════════════════════════════════════════

echo
echo "=== A. baseline paths (must be identical before/after the patch) ==="
expect 1222 "a outE flow, no condition"                                  "${V[@]}"
expect 1    "asset=ETC & epoch=100 (full equality -> IdPrefixQuery)"      "${V[@]}" --data-urlencode 'properties={"asset":"ETC","epoch":100}'
expect 1    "asset=ETC & epoch=Long.MAX (full equality)"                  "${V[@]}" --data-urlencode 'properties={"asset":"ETC","epoch":9223372036854775807}'
expect 11   "epoch=100 only (not a prefix -> in-memory filter)"           "${V[@]}" --data-urlencode 'properties={"epoch":100}'
expect 121  "cnt=7 only (plain property, in-memory; 120 bulk + 1)"        "${V[@]}" --data-urlencode 'properties={"cnt":7}'

echo
echo "=== B. sort-key prefix equality (IdRangeQuery) — exactness of the range on strings ==="
expect 1212 "asset=ETC"                                                   "${V[@]}" --data-urlencode 'properties={"asset":"ETC"}'
expect 1    "asset=ET   (must NOT swallow ETC*)"                          "${V[@]}" --data-urlencode 'properties={"asset":"ET"}'
expect 1    "asset=ETCX"                                                  "${V[@]}" --data-urlencode 'properties={"asset":"ETCX"}'
expect 1    "asset=etc  (case-sensitive)"                                 "${V[@]}" --data-urlencode 'properties={"asset":"etc"}'
expect 1    "asset=Etc"                                                   "${V[@]}" --data-urlencode 'properties={"asset":"Etc"}'
expect 1    "asset='ETC ' (trailing space)"                               "${V[@]}" --data-urlencode 'properties={"asset":"ETC "}'
expect 1    "asset='ETC!X' (separator char inside value) [informational]" "${V[@]}" --data-urlencode 'properties={"asset":"ETC!X"}'
expect 1    "asset=BTC"                                                   "${V[@]}" --data-urlencode 'properties={"asset":"BTC"}'
expect 1    "asset=ÉTC (non-ASCII, 2-byte UTF-8)"                         "${V[@]}" --data-urlencode 'properties={"asset":"ÉTC"}'
expect 1    "asset=～  (U+FF5E, 3-byte UTF-8, high UTF-16 unit)"          "${V[@]}" --data-urlencode 'properties={"asset":"～"}'
expect 1    "asset=😀  (U+1F600, 4-byte UTF-8, surrogate pair)"           "${V[@]}" --data-urlencode 'properties={"asset":"😀"}'
expect 0    "asset=XYZ (no such prefix)"                                  "${V[@]}" --data-urlencode 'properties={"asset":"XYZ"}'

echo
echo "=== C. ranges on the LONG sort key (LongEncoding boundaries) ==="
expect 1207 "asset=ETC & epoch>=150"                                      "${V[@]}" --data-urlencode 'properties={"asset":"ETC","epoch":"P.gte(150)"}'
expect 1    "asset=ETC & epoch<0   (negative encoding)"                   "${V[@]}" --data-urlencode 'properties={"asset":"ETC","epoch":"P.lt(0)"}'
expect 2    "asset=ETC & epoch<=0"                                        "${V[@]}" --data-urlencode 'properties={"asset":"ETC","epoch":"P.lte(0)"}'
expect 1211 "asset=ETC & epoch>=0"                                        "${V[@]}" --data-urlencode 'properties={"asset":"ETC","epoch":"P.gte(0)"}'
expect 2    "asset=ETC & 63<=epoch<=64   (1-digit/2-digit encoding edge)" "${V[@]}" --data-urlencode 'properties={"asset":"ETC","epoch":"P.between(63,65)"}'
expect 2    "asset=ETC & 4095<=epoch<=4096 (2-/3-digit encoding edge)"    "${V[@]}" --data-urlencode 'properties={"asset":"ETC","epoch":"P.between(4095,4097)"}'
expect 2    "asset=ETC & epoch>=4096 (4096 + Long.MAX)"                   "${V[@]}" --data-urlencode 'properties={"asset":"ETC","epoch":"P.gte(4096)"}'
expect 1    "asset=ETC & epoch between(100,200) [gte,lt)"                 "${V[@]}" --data-urlencode 'properties={"asset":"ETC","epoch":"P.between(100,200)"}'
expect 0    "asset=ETC & epoch inside(100,200) (gt,lt)"                   "${V[@]}" --data-urlencode 'properties={"asset":"ETC","epoch":"P.inside(100,200)"}'
expect 1000 "asset=ETC & 1000<=epoch<2000 (bulk, >1 gRPC batch)"          "${V[@]}" --data-urlencode 'properties={"asset":"ETC","epoch":"P.between(1000,2000)"}'
expect 0    "asset=ETC & epoch>=3000 & epoch<4000 (empty range)"          "${V[@]}" --data-urlencode 'properties={"asset":"ETC","epoch":"P.between(3000,4000)"}'

echo
echo "=== D. ranges on the STRING sort key (compare() Comparable path on raw strings) ==="
# byte-order semantics: ETC*(1212) + 'ETC '(1) + 'ETC!X'(1) + ETCX(1) + Etc(1) + etc(1) + ÉTC(1) + ～(1) + 😀(1)
expect 1220 "asset>=ETC"                                                  "${V[@]}" --data-urlencode 'properties={"asset":"P.gte(\"ETC\")"}'
expect 1213 "asset>=ETC & asset<ETC!  (ETC x1212 + 'ETC ')"              "${V[@]}" --data-urlencode 'properties={"asset":"P.between(\"ETC\",\"ETC!\")"}'
expect 3    "asset>=ÉTC  (ÉTC, ～, 😀 in UTF-8 byte order) [informational]" "${V[@]}" --data-urlencode 'properties={"asset":"P.gte(\"ÉTC\")"}'
expect 2    "asset>=～   (～ < 😀 in UTF-8 bytes, but ～ > 😀 in UTF-16!) [informational]" "${V[@]}" --data-urlencode 'properties={"asset":"P.gte(\"～\")"}'

echo
echo "=== E. IN / within lists (prepareConditionQueryList path) ==="
expect 2    "asset within(ETC,BTC) & epoch=100"                           "${V[@]}" --data-urlencode 'properties={"asset":"P.within(\"ETC\",\"BTC\")","epoch":100}'
expect 2    "asset within(ET,ETCX) (prefix list)"                         "${V[@]}" --data-urlencode 'properties={"asset":"P.within(\"ET\",\"ETCX\")"}'

echo
echo "=== F. sort-key range combined with a plain property (core must keep filtering cnt) ==="
expect 120  "asset=ETC & epoch>=1000 & cnt=5"                             "${V[@]}" --data-urlencode 'properties={"asset":"ETC","epoch":"P.gte(1000)","cnt":5}'

echo
echo "=== G. direction IN on b, BOTH via Gremlin ==="
expect 1212 "b inE flow asset=ETC"                                        "${VB[@]}" --data-urlencode 'properties={"asset":"ETC"}'
expect 1207 "b inE flow asset=ETC & epoch>=150"                           "${VB[@]}" --data-urlencode 'properties={"asset":"ETC","epoch":"P.gte(150)"}'
gexpect 1212 "g.V('a').bothE('flow').has('asset','ETC')"                  "g.V('a').bothE('flow').has('asset','ETC')"
gexpect 1207 "g.V('a').outE('flow').has('asset','ETC').has('epoch',gte(150))" "g.V('a').outE('flow').has('asset','ETC').has('epoch',gte(150))"
gexpect 2    "g.V('a').outE('flow').has('asset','ETC').has('epoch',between(63,65))" "g.V('a').outE('flow').has('asset','ETC').has('epoch',between(63,65))"
gexpect 2    "g.V('a').outE('flow').has('asset',within('ET','ETCX'))"     "g.V('a').outE('flow').has('asset',within('ET','ETCX'))"
gexpect 1    "g.V('a').outE('flow').has('asset','ETC').has('epoch',100) (control)" "g.V('a').outE('flow').has('asset','ETC').has('epoch',100)"

PNODE_VID=$("${CURL[@]}" -G "${BASE}/graph/vertices" --data-urlencode 'label=pnode' --data-urlencode 'limit=1' | grep -o '"id":"[0-9]*:alice"' | head -1 | cut -d'"' -f4)
PNODE_VID=${PNODE_VID:-4:alice}
echo
echo "=== H. other owner-vertex id types (equals() on OWNER_VERTEX) and 2-digit label id (SUB_LABEL=10) ==="
expect 2 "nnode 7 outE flow_n asset=ETC (CUSTOMIZE_NUMBER)"              --data-urlencode 'vertex_id=7' --data-urlencode 'direction=OUT' --data-urlencode 'label=flow_n' --data-urlencode 'properties={"asset":"ETC"}'
expect 1 "nnode 7 outE flow_n asset=ETC & epoch>=150"                    --data-urlencode 'vertex_id=7' --data-urlencode 'direction=OUT' --data-urlencode 'label=flow_n' --data-urlencode 'properties={"asset":"ETC","epoch":"P.gte(150)"}'
expect 2 "unode outE flow_u asset=ETC (CUSTOMIZE_UUID)"                  --data-urlencode 'vertex_id=U"550e8400-e29b-41d4-a716-446655440000"' --data-urlencode 'direction=OUT' --data-urlencode 'label=flow_u' --data-urlencode 'properties={"asset":"ETC"}'
expect 1 "unode outE flow_u asset=ETC & epoch>=150"                      --data-urlencode 'vertex_id=U"550e8400-e29b-41d4-a716-446655440000"' --data-urlencode 'direction=OUT' --data-urlencode 'label=flow_u' --data-urlencode 'properties={"asset":"ETC","epoch":"P.gte(150)"}'
expect 2 "pnode 4:alice outE flow_p asset=ETC (PRIMARY_KEY, label id 10)" --data-urlencode "vertex_id=\"${PNODE_VID}\"" --data-urlencode 'direction=OUT' --data-urlencode 'label=flow_p' --data-urlencode 'properties={"asset":"ETC"}'
expect 1 "pnode 4:alice outE flow_p asset=ETC & epoch>=150"              --data-urlencode "vertex_id=\"${PNODE_VID}\"" --data-urlencode 'direction=OUT' --data-urlencode 'label=flow_p' --data-urlencode 'properties={"asset":"ETC","epoch":"P.gte(150)"}'
expect 8 "b inE (all labels) asset=ETC & epoch>=100 & epoch<=200 (multi-label, SUB_LABEL 1/8/9/10; 2 per label)" --data-urlencode 'vertex_id="b"' --data-urlencode 'direction=IN' --data-urlencode 'properties={"asset":"ETC","epoch":"P.between(100,201)"}'

echo
echo "=== I. paging (position-carrying scan variant of queryByRange) ==="
pexpect 1212 "asset=ETC, page size 500"                                  500 "${V[@]}" --data-urlencode 'properties={"asset":"ETC"}'
pexpect 1207 "asset=ETC & epoch>=150, page size 100"                     100 "${V[@]}" --data-urlencode 'properties={"asset":"ETC","epoch":"P.gte(150)"}'
pexpect 1000 "asset=ETC & 1000<=epoch<2000, page size 333"               333 "${V[@]}" --data-urlencode 'properties={"asset":"ETC","epoch":"P.between(1000,2000)"}'

echo
printf '=== SUMMARY: PASS=%d  FAIL=%d  ERR=%d ===\n' "$PASS" "$FAIL" "$ERR"
[[ $FAIL -eq 0 && $ERR -eq 0 ]]
