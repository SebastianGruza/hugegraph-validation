#!/usr/bin/env bash
# Phase C: stop 1.7.0 release cluster without wiping, swap lib/ to master jars everywhere, restart, read.
set -uo pipefail
N1=seba@192.168.80.235; N2=seba@192.168.80.236; N3=seba@192.168.80.237
SSH="ssh -n -o ConnectTimeout=15 -o BatchMode=yes"
H=/home/seba; R=$H/rel-1.7.0/apache-hugegraph-incubating-1.7.0
PD=$R/apache-hugegraph-pd-incubating-1.7.0; ST1=$R/apache-hugegraph-store-incubating-1.7.0; STN=$H/rel-1.7.0/apache-hugegraph-store-incubating-1.7.0
SV=$R/apache-hugegraph-server-incubating-1.7.0; SVR=$R/server-rocksdb-1.7.0
M=$H/mst
J17="export JAVA_HOME=$H/tools/jdk17 PATH=$H/tools/jdk17/bin:/usr/bin:/bin:/usr/sbin"
J11="export JAVA_HOME=$H/tools/jdk11 PATH=$H/tools/jdk11/bin:/usr/bin:/bin:/usr/sbin LC_ALL=C.UTF-8 LANG=C.UTF-8"
pk() { echo "for p in $*; do P=\$(ss -tlnp 2>/dev/null | grep \":\$p \" | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2); [ -n \"\$P\" ] && kill \$P; done"; }
echo "== $(date +%T) unpack master tarballs on .235"
$SSH $N1 "rm -rf $M && mkdir -p $M && cd $M && tar xzf $H/hg-master/hugegraph-pd/apache-hugegraph-pd-1.7.0.tar.gz && tar xzf $H/hg-master/hugegraph-store/apache-hugegraph-store-1.7.0.tar.gz && tar xzf $H/hg-master/hugegraph-server/apache-hugegraph-server-1.7.0.tar.gz && ls $M && (cd $M/apache-hugegraph-server-1.7.0/lib && ls hugegraph-core-*.jar) && (cd $H/hg-master && git log --oneline -1)"
echo "== $(date +%T) copy master store lib to node2/node3"
for N in $N2 $N3; do $SSH $N1 "cd $M/apache-hugegraph-store-1.7.0 && tar cf - lib" | $SSH $N "rm -rf $H/mst-store-lib && mkdir -p $H/mst-store-lib && cd $H/mst-store-lib && tar xf - && ls lib | wc -l"; done
echo "== $(date +%T) stop servers, stores, PD (NO wipe)"
$SSH $N1 "$J11; for D in $SV $SVR; do (cd \$D && ./bin/stop-hugegraph.sh) >/dev/null 2>&1; rm -f \$D/bin/pid; done; sleep 3; $(pk 8080 8081 8182 8183); echo servers-stopped"
for N in $N1 $N2 $N3; do D=$STN; [ $N = $N1 ] && D=$ST1; $SSH $N "(cd $D && ./bin/stop-hugegraph-store.sh) >/dev/null 2>&1; sleep 2; $(pk 8500); rm -f $D/bin/pid; echo \"store stopped \$(hostname)\""; done
$SSH $N1 "(cd $PD && ./bin/stop-hugegraph-pd.sh) >/dev/null 2>&1; sleep 2; $(pk 8686 8620); rm -f $PD/bin/pid; echo pd-stopped; du -sh $PD/pd_data $ST1/storage $SVR/rocksdb-data"
echo "== $(date +%T) swap lib/ -> master"
$SSH $N1 "set -e; cd $PD && [ -d lib-1.7.0 ] || mv lib lib-1.7.0; rm -rf lib; cp -r $M/apache-hugegraph-pd-1.7.0/lib lib; cd $ST1 && [ -d lib-1.7.0 ] || mv lib lib-1.7.0; rm -rf lib; cp -r $M/apache-hugegraph-store-1.7.0/lib lib; for D in $SV $SVR; do cd \$D && ([ -d lib-1.7.0 ] || mv lib lib-1.7.0); rm -rf lib; cp -r $M/apache-hugegraph-server-1.7.0/lib lib; done; echo swapped-235; ls $SV/lib | grep -c jar"
for N in $N2 $N3; do $SSH $N "set -e; cd $STN && ([ -d lib-1.7.0 ] || mv lib lib-1.7.0); rm -rf lib; cp -r $H/mst-store-lib/lib lib; echo \"swapped \$(hostname)\""; done
echo "== $(date +%T) start PD (master jars, 1.7.0 conf+data)"
$SSH $N1 "$J17; cd $PD && rm -f logs/*.log; ./bin/start-hugegraph-pd.sh >/dev/null 2>&1; for i in \$(seq 1 20); do ss -tln | grep -q ':8686 ' && break; sleep 2; done; ss -tln | grep -q ':8686 ' && echo pd-up || (echo PD-DOWN; tail -5 logs/*.log)"
sleep 8
echo "== $(date +%T) start stores"
for N in $N1 $N2 $N3; do D=$STN; [ $N = $N1 ] && D=$ST1; $SSH $N "$J17; cd $D && rm -f logs/*.log; ./bin/start-hugegraph-store.sh >/dev/null 2>&1; for i in \$(seq 1 30); do ss -tln | grep -q ':8500 ' && break; sleep 2; done; ss -tln | grep -q ':8500 ' && echo \"store-up \$(hostname)\" || (echo \"STORE-DOWN \$(hostname)\"; tail -3 logs/*.log)"; done
sleep 10
echo "== $(date +%T) start servers (no init-store)"
$SSH $N1 "$J11; for D in $SV $SVR; do cd \$D && rm -f logs/*.log bin/pid; ./bin/start-hugegraph.sh 2>&1 | tail -1; done; for i in \$(seq 1 40); do curl -sf http://127.0.0.1:8080/graphs >/dev/null && curl -sf http://127.0.0.1:8081/graphs >/dev/null && break; sleep 3; done; echo \"8080: \$(curl -s --compressed http://127.0.0.1:8080/graphs) \$(curl -s --compressed http://127.0.0.1:8080/versions)\"; echo \"8081: \$(curl -s --compressed http://127.0.0.1:8081/graphs) \$(curl -s --compressed http://127.0.0.1:8081/versions)\""
echo "== $(date +%T) read on master"
$SSH $N1 "for b in 8080 8081; do python3 ~/hg_data.py read http://127.0.0.1:\$b ~/upg/D-master-\$b-read.json 2>&1 | cut -c1-170; echo \"--- \$b read done\"; done"
