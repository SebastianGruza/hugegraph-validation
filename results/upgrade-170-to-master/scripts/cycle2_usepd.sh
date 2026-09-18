#!/usr/bin/env bash
# Cycle 2: usePD=true. 1.7.0 jars -> write; swap lib to master -> read. hstore only.
set -uo pipefail
N1=seba@192.168.80.235; N2=seba@192.168.80.236; N3=seba@192.168.80.237
SSH="ssh -n -o ConnectTimeout=15 -o BatchMode=yes"
H=/home/seba; R=$H/rel-1.7.0/apache-hugegraph-incubating-1.7.0; M=$H/mst
PD=$R/apache-hugegraph-pd-incubating-1.7.0; ST1=$R/apache-hugegraph-store-incubating-1.7.0; STN=$H/rel-1.7.0/apache-hugegraph-store-incubating-1.7.0
SV=$R/apache-hugegraph-server-incubating-1.7.0; SVR=$R/server-rocksdb-1.7.0
J17="export JAVA_HOME=$H/tools/jdk17 PATH=$H/tools/jdk17/bin:/usr/bin:/bin:/usr/sbin"
J11="export JAVA_HOME=$H/tools/jdk11 PATH=$H/tools/jdk11/bin:/usr/bin:/bin:/usr/sbin LC_ALL=C.UTF-8 LANG=C.UTF-8"
pk() { echo "for p in $*; do P=\$(ss -tlnp 2>/dev/null | grep \":\$p \" | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2); [ -n \"\$P\" ] && kill -9 \$P; done"; }
stop_all() {
  $SSH $N1 "for D in $SV $SVR; do (cd \$D && ./bin/stop-hugegraph.sh) >/dev/null 2>&1; rm -f \$D/bin/pid; done; sleep 2; $(pk 8080 8081 8182 8183); echo servers-stopped"
  for N in $N1 $N2 $N3; do D=$STN; [ $N = $N1 ] && D=$ST1; $SSH $N "(cd $D && ./bin/stop-hugegraph-store.sh) >/dev/null 2>&1; sleep 1; $(pk 8500); rm -f $D/bin/pid; echo \"store stopped \$(hostname)\""; done
  $SSH $N1 "(cd $PD && ./bin/stop-hugegraph-pd.sh) >/dev/null 2>&1; sleep 1; $(pk 8686 8620); rm -f $PD/bin/pid; echo pd-stopped"
}
use_lib() { # $1 = 1.7.0 | master
  if [ "$1" = "1.7.0" ]; then
    $SSH $N1 "for D in $PD $ST1 $SV; do cd \$D && rm -rf lib && cp -r lib-1.7.0 lib; done; echo lib-1.7.0-restored-235"
    for N in $N2 $N3; do $SSH $N "cd $STN && rm -rf lib && cp -r lib-1.7.0 lib && echo \"lib-1.7.0 \$(hostname)\""; done
  else
    $SSH $N1 "cd $PD && rm -rf lib && cp -r $M/apache-hugegraph-pd-1.7.0/lib lib; cd $ST1 && rm -rf lib && cp -r $M/apache-hugegraph-store-1.7.0/lib lib; cd $SV && rm -rf lib && cp -r $M/apache-hugegraph-server-1.7.0/lib lib; echo master-lib-235"
    for N in $N2 $N3; do $SSH $N "cd $STN && rm -rf lib && cp -r $H/mst-store-lib/lib lib && echo \"master-lib \$(hostname)\""; done
  fi
}
start_all() { # $1 tag
  $SSH $N1 "$J17; cd $PD && rm -f logs/*.log; ./bin/start-hugegraph-pd.sh >/dev/null 2>&1; for i in \$(seq 1 20); do ss -tln | grep -q ':8686 ' && break; sleep 2; done; ss -tln | grep -q ':8686 ' && echo pd-up || (echo PD-DOWN; tail -5 logs/*.log)"
  sleep 8
  for N in $N1 $N2 $N3; do D=$STN; [ $N = $N1 ] && D=$ST1; $SSH $N "$J17; cd $D && rm -f logs/*.log; ./bin/start-hugegraph-store.sh >/dev/null 2>&1; for i in \$(seq 1 30); do ss -tln | grep -q ':8500 ' && break; sleep 2; done; ss -tln | grep -q ':8500 ' && echo \"store-up \$(hostname)\" || (echo \"STORE-DOWN \$(hostname)\"; tail -3 logs/*.log)"; done
  sleep 12
  $SSH $N1 "$J11; cd $SV && rm -f logs/*.log bin/pid; if [ '$2' = init ]; then for i in 1 2 3 4 5 6; do echo '' | ./bin/init-store.sh > /tmp/init_$1.log 2>&1; grep -q 'less then 3\|error code = 105\|PD unreachable' /tmp/init_$1.log || break; sleep 10; done; tail -2 /tmp/init_$1.log; fi; ./bin/start-hugegraph.sh 2>&1 | tail -1; for i in \$(seq 1 40); do curl -sf http://127.0.0.1:8080/graphs >/dev/null && break; sleep 3; done; echo \"8080: \$(curl -s --compressed http://127.0.0.1:8080/graphs) \$(curl -s --compressed http://127.0.0.1:8080/versions)\""
}
echo "== $(date +%T) stop + restore 1.7.0 libs + wipe"
stop_all; use_lib 1.7.0
$SSH $N1 "rm -rf $PD/pd_data $ST1/storage; cd $SV && grep -q '^usePD' conf/rest-server.properties || printf 'usePD=true\npd.peers=192.168.80.235:8686\n' >> conf/rest-server.properties; grep -n 'usePD\|pd.peers\|^cluster' conf/rest-server.properties"
for N in $N2 $N3; do $SSH $N "rm -rf $STN/storage"; done
echo "== $(date +%T) start 1.7.0 (usePD=true)"
start_all rel170 init
echo "== $(date +%T) write on 1.7.0"
$SSH $N1 "python3 ~/hg_data.py write http://127.0.0.1:8080 ~/upg/C2-170-write.json 2>&1 | grep -v '^20[0-2]' | cut -c1-160; grep -c '' ~/upg/C2-170-write.json"
$SSH $N1 "grep -n 'cluster\|connect\|MetaManager' $SV/logs/hugegraph-server.log | head -8 | cut -c1-200"
echo "== $(date +%T) stop, swap to master libs, start (no init)"
stop_all; use_lib master
start_all master noinit
echo "== $(date +%T) read on master"
$SSH $N1 "python3 ~/hg_data.py read http://127.0.0.1:8080 ~/upg/C2-master-read.json 2>&1 | cut -c1-170"
$SSH $N1 "grep -in 'error\|warn' $SV/logs/hugegraph-server.log | grep -v redundant | head -12 | cut -c1-220"
