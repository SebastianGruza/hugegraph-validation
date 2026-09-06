#!/usr/bin/env bash
# pd_watch_exp2.sh <side> <distA> <distB> [outage_s] [tag] — PD KV watch recovery experiment (PR #3157 / issue #3152)
#   Two hstore servers on one PD in PD-meta mode (usePD=true). Observable that is NOT compensated by lazy loading:
#   A creates graph g, B constructs it on first access (lazy load from PD meta), A DROPS g -> B must drop its in-memory
#   instance on the GRAPH/REMOVE watch event. Dead watch => B keeps serving the dropped graph (stale HTTP 200).
#   Steps: baseline -> PD outage #1 -> probe -> PD outage #2 -> probe.
set -uo pipefail
SIDE=${1:?side}; A=${2:?distA}; B=${3:?distB}; OUT=${4:-30}; TAG=${5:-$(date +%H%M)}
N=${SIDE}_${TAG}
H=$HOME; PD=$H/hugegraph/hugegraph-pd/apache-hugegraph-pd-1.7.0
G=graphspaces/DEFAULT/graphs
CONFIRM='I%27m%20sure%20to%20drop%20the%20graph'
export JAVA_HOME=$H/tools/jdk11 PATH=$H/tools/jdk11/bin:/usr/bin:/bin:/usr/sbin
export LC_ALL=C.UTF-8 LANG=C.UTF-8   # GraphSpace.info() parses a locale-formatted float; pl_PL breaks usePD startup
ts() { date +%T; }
port_pid() { ss -tlnp 2>/dev/null | grep ":$1 " | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2; }
stop_servers() {
  for D in "$A" "$B"; do (cd "$D" && ./bin/stop-hugegraph.sh) >/dev/null 2>&1; done
  for p in 8080 8081 8082 8182 8183 8184; do P=$(port_pid $p); [ -n "$P" ] && kill -9 "$P" 2>/dev/null; done
  sleep 2; for D in "$A" "$B"; do rm -f "$D/bin/pid"; done
}
wait_rest() { for i in $(seq 1 40); do curl -sf "http://127.0.0.1:$1/$G" >/dev/null && return 0; sleep 3; done; return 1; }
code_B() { curl -s -o /dev/null -w '%{http_code}' --compressed "http://127.0.0.1:8082/$G/$1/schema/propertykeys"; }
signals() { echo "add=$(grep -c "Accept graph add signal from etcd for DEFAULT-$1" "$B"/logs/hugegraph-server.log) remove=$(grep -c "DEFAULT-$1" "$B"/logs/hugegraph-server.log | tr -d '\n')"; }
probe() {   # $1 graph name: create on A, construct on B, drop on A, expect B -> 404 via REMOVE event
  local g=$1
  curl -s -o /dev/null -X POST -H 'Content-Type: application/json' "http://127.0.0.1:8080/$G/$g" \
       -d "{\"backend\":\"hstore\",\"serializer\":\"binary\",\"store\":\"$g\",\"pd.peers\":\"192.168.80.235:8686\"}" --max-time 60
  local c; c=$(code_B "$g"); echo "  A created $g; B first access -> HTTP $c (lazy load from PD meta)"
  curl -s -o /dev/null -X DELETE "http://127.0.0.1:8080/$G/$g?confirm_message=$CONFIRM" --max-time 60
  echo "  A dropped $g at $(ts)"
  for i in $(seq 1 60); do
    c=$(code_B "$g")
    if [ "$c" != "200" ]; then echo "  B dropped $g after ${i}s (HTTP $c)  [watch ALIVE]  log: $(signals $g)"; return 0; fi
    sleep 1
  done
  echo "  B STILL SERVES $g after 60 s (HTTP $c)  [watch DEAD]  log: $(signals $g)"; return 1
}
pd_outage() {
  echo "  [$(ts)] PD down for $1 s"
  (cd "$PD" && ./bin/stop-hugegraph-pd.sh) >/dev/null 2>&1; P=$(port_pid 8686); [ -n "$P" ] && kill -9 "$P"; sleep 1
  sleep "$1"
  export JAVA_HOME=$H/tools/jdk17 PATH=$H/tools/jdk17/bin:/usr/bin:/bin:/usr/sbin
  rm -f "$PD/bin/pid"; (cd "$PD" && ./bin/start-hugegraph-pd.sh) >/dev/null 2>&1
  for i in $(seq 1 30); do ss -tln | grep -q ':8686 ' && break; sleep 2; done
  export JAVA_HOME=$H/tools/jdk11 PATH=$H/tools/jdk11/bin:/usr/bin:/bin:/usr/sbin
  echo "  [$(ts)] PD back ($(ss -tln | grep -q ':8686 ' && echo listening || echo NOT LISTENING)); settling 15 s"; sleep 15
  echo "  stores: $(for h in 235 236 237; do printf '%s ' $(timeout 2 bash -c "</dev/tcp/192.168.80.$h/8500" 2>/dev/null && echo up || echo DOWN); done)"
}
echo "== [$SIDE] $(ts) start A=$A B=$B"
stop_servers
for D in "$A" "$B"; do (cd "$D" && ./bin/start-hugegraph.sh) >/dev/null 2>&1; done
wait_rest 8080 && wait_rest 8082 && echo "  A+B up" || { echo "  SERVERS NOT UP"; exit 1; }
echo "== [$SIDE] step 1: baseline (create -> B loads -> drop -> B must drop via REMOVE event)"; probe "${N}_g1"
echo "== [$SIDE] step 2: PD outage #1"; pd_outage "$OUT"
echo "== [$SIDE] step 3: after outage #1"; probe "${N}_g2"
echo "== [$SIDE] step 4: PD outage #2"; pd_outage "$OUT"
echo "== [$SIDE] step 5: after outage #2"; probe "${N}_g3"
echo "== [$SIDE] B log — KvClient lines since start:"
grep -hE "KvClient" "$B"/logs/hugegraph-server.log | grep -vE "wait for client" | sed -E 's/^([0-9-]+ [0-9:]+).*\] /\1 /' | cut -c1-150 | tail -n 12
echo "== [$SIDE] $(ts) done (servers left running)"
