#!/usr/bin/env bash
# Phase A: bring up the official 1.7.0 release: PD (.235), stores (.235/.236/.237), server hstore :8080, server rocksdb :8081
set -uo pipefail
N1=seba@192.168.80.235; N2=seba@192.168.80.236; N3=seba@192.168.80.237
SSH="ssh -n -o ConnectTimeout=15 -o BatchMode=yes"
H=/home/seba; R=$H/rel-1.7.0/apache-hugegraph-incubating-1.7.0
PD=$R/apache-hugegraph-pd-incubating-1.7.0; ST=$R/apache-hugegraph-store-incubating-1.7.0
SV=$R/apache-hugegraph-server-incubating-1.7.0; SVR=$R/server-rocksdb-1.7.0
J17="export JAVA_HOME=$H/tools/jdk17 PATH=$H/tools/jdk17/bin:/usr/bin:/bin:/usr/sbin"
J11="export JAVA_HOME=$H/tools/jdk11 PATH=$H/tools/jdk11/bin:/usr/bin:/bin:/usr/sbin LC_ALL=C.UTF-8 LANG=C.UTF-8"
echo "== $(date +%T) configure PD + start"
$SSH $N1 "cp $H/hugegraph/hugegraph-pd/apache-hugegraph-pd-1.7.0/conf/application.yml $PD/conf/application.yml; rm -rf $PD/pd_data $PD/logs/*; $J17; cd $PD && ./bin/start-hugegraph-pd.sh >/dev/null 2>&1; for i in \$(seq 1 20); do ss -tln | grep -q ':8686 ' && break; sleep 2; done; ss -tln | grep -q ':8686 ' && echo pd-up || (echo PD-DOWN; tail -5 logs/*.log)"
sleep 8
echo "== $(date +%T) configure stores + start"
for N in $N1 $N2 $N3; do $SSH $N "cp $H/hugegraph/hugegraph-store/apache-hugegraph-store-1.7.0/conf/application.yml $ST/conf/application.yml; rm -rf $ST/storage $ST/logs/*; $J17; cd $ST && ./bin/start-hugegraph-store.sh >/dev/null 2>&1; for i in \$(seq 1 30); do ss -tln | grep -q ':8500 ' && break; sleep 2; done; ss -tln | grep -q ':8500 ' && echo \"store-up \$(hostname)\" || echo \"STORE-DOWN \$(hostname)\""; done
sleep 10
echo "== $(date +%T) configure servers"
$SSH $N1 "cp $H/hg-master/hugegraph-server/dist-master1a15-hstore/conf/graphs/hugegraph.properties $SV/conf/graphs/hugegraph.properties; rm -rf $SV/logs/* $SV/bin/pid;
  rm -rf $SVR; cp -r $SV $SVR; cd $SVR; sed -i 's#restserver.url=http://127.0.0.1:8080#restserver.url=http://127.0.0.1:8081\ngremlinserver.url=127.0.0.1:8183#' conf/rest-server.properties; sed -i 's/^#host: 127.0.0.1/host: 127.0.0.1/; s/^#port: 8182/port: 8183/' conf/gremlin-server.yaml; cp $R/apache-hugegraph-server-incubating-1.7.0/conf/graphs/hugegraph.properties /tmp/x; sed -i 's/^backend=hstore/backend=rocksdb/; /^pd.peers/d' conf/graphs/hugegraph.properties; grep -n 'backend=\|pd.peers\|restserver.url\|gremlinserver' conf/graphs/hugegraph.properties conf/rest-server.properties; grep -n '^host\|^port' conf/gremlin-server.yaml; rm -rf rocksdb-data logs/* bin/pid"
echo "== $(date +%T) init-store + start hstore server"
$SSH $N1 "$J11; cd $SV; for i in 1 2 3 4 5 6; do echo '' | ./bin/init-store.sh > /tmp/init_rel_hstore.log 2>&1; grep -q 'less then 3\|error code = 105\|PD unreachable' /tmp/init_rel_hstore.log || break; echo '  init retry'; sleep 10; done; tail -3 /tmp/init_rel_hstore.log; ./bin/start-hugegraph.sh 2>&1 | tail -2"
echo "== $(date +%T) init-store + start rocksdb server"
$SSH $N1 "$J11; cd $SVR; echo '' | ./bin/init-store.sh > /tmp/init_rel_rocks.log 2>&1; tail -2 /tmp/init_rel_rocks.log; ./bin/start-hugegraph.sh 2>&1 | tail -2"
$SSH $N1 "for i in \$(seq 1 30); do curl -sf http://127.0.0.1:8080/graphs >/dev/null && curl -sf http://127.0.0.1:8081/graphs >/dev/null && break; sleep 3; done; echo \"8080: \$(curl -s --compressed http://127.0.0.1:8080/graphs) | \$(curl -s --compressed http://127.0.0.1:8080/versions)\"; echo \"8081: \$(curl -s --compressed http://127.0.0.1:8081/graphs)\""
