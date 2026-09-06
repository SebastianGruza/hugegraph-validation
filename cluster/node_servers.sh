#!/usr/bin/env bash
# node_servers.sh stop | start <side> — na .235: serwery hugegraph (8080 hstore, 8081 rocksdb) i PD
#   stop : skrypty stop -> kill po PID portow -> wipe pd_data, rocksdb-data, logi, pid-y
#   start: init-store hstore (retry gdy PD zglasza <3 store'ow), init rocksdb, start obu, czekaj na REST
set -uo pipefail
H=$HOME
PD=$H/hugegraph/hugegraph-pd/apache-hugegraph-pd-1.7.0
# ktore dystrybucje serwera (domyslnie build combined); dla strony master driver podaje dist-master-*
SV=${HG_SV:-$H/hugegraph/hugegraph-server/apache-hugegraph-server-1.7.0}
RD=${HG_RD:-$H/hugegraph/hugegraph-server/dist-rocksdb}
port_pid() { ss -tlnp 2>/dev/null | grep ":$1 " | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2; }
case ${1:?stop|start} in
  stop)
    (cd "$SV" && ./bin/stop-hugegraph.sh) >/dev/null 2>&1
    (cd "$RD" && ./bin/stop-hugegraph.sh) >/dev/null 2>&1
    (cd "$PD" && ./bin/stop-hugegraph-pd.sh) >/dev/null 2>&1
    sleep 3
    for i in $(seq 1 15); do
      for p in 8080 8081 8182 8183 8686 8620; do P=$(port_pid $p); [ -n "$P" ] && kill -9 "$P" 2>/dev/null; done
      for Q in $(pgrep -f 'hg-pd-servic[e]') $(pgrep -f 'apache-hugegraph-p[d]'); do kill -9 "$Q" 2>/dev/null; done
      ss -tln | grep -qE ':(8080|8081|8182|8183|8686|8620) ' || break
      sleep 2
    done
    rm -rf "$PD/pd_data" "$PD/bin/pid" "$SV/bin/pid" "$RD/rocksdb-data" "$RD/bin/pid"
    rm -f "$PD"/logs/*.log "$SV"/logs/*.log "$RD"/logs/*.log
    echo "  servers+pd stopped, data wiped ($(ss -tln | grep -qE ':(8080|8081|8686) ' && echo 'PORTS STILL OPEN' || echo 'ports free'))"
    ;;
  start)
    SIDE=${2:-combined}
    export JAVA_HOME=$H/tools/jdk11 PATH=$H/tools/jdk11/bin:/usr/bin:/bin:/usr/sbin
    for i in 1 2 3 4 5 6 7 8; do
      (cd "$SV" && echo "" | ./bin/init-store.sh) > /tmp/init_${SIDE}_hstore.log 2>&1
      grep -q 'less then 3\|error code = 105\|PD unreachable' /tmp/init_${SIDE}_hstore.log || break
      echo "  init-store hstore retry $i (cluster not ready)"; sleep 10
    done
    (cd "$RD" && echo "" | ./bin/init-store.sh) > /tmp/init_${SIDE}_rocksdb.log 2>&1
    (cd "$SV" && ./bin/start-hugegraph.sh) >/dev/null 2>&1
    (cd "$RD" && ./bin/start-hugegraph.sh) >/dev/null 2>&1
    for i in $(seq 1 30); do
      curl -sf http://127.0.0.1:8080/graphspaces/DEFAULT/graphs/hugegraph/schema/propertykeys >/dev/null &&
      curl -sf http://127.0.0.1:8081/graphspaces/DEFAULT/graphs/hugegraph/schema/propertykeys >/dev/null && break
      sleep 3
    done
    echo "  8080: $(curl -s --compressed http://127.0.0.1:8080/graphspaces/DEFAULT/graphs/hugegraph | head -c 60)"
    echo "  8081: $(curl -s --compressed http://127.0.0.1:8081/graphspaces/DEFAULT/graphs/hugegraph | head -c 60)"
    # smoke: zapis na hstore musi przejsc (partycje przydzielone = 3 store'y aktywne)
    R=$(curl -s --compressed -X POST -H 'Content-Type: application/json' http://127.0.0.1:8080/graphspaces/DEFAULT/graphs/hugegraph/schema/propertykeys -d '{"name":"_smoke","data_type":"INT","cardinality":"SINGLE"}' --max-time 60 | head -c 100)
    echo "  hstore write smoke: $R"
    ;;
esac
