#!/usr/bin/env bash
# restart_nowipe.sh — restart the whole 3-store cluster WITHOUT deleting any data (pd_data / storage / rocksdb-data
# all preserved). Orchestrated from the workstation. Stores on .235/.236/.237, PD+servers on .235.
set -uo pipefail
N1=seba@192.168.80.235; N2=seba@192.168.80.236; N3=seba@192.168.80.237
SSH="ssh -n -o ConnectTimeout=15 -o BatchMode=yes"
H=/home/seba
PD=$H/hugegraph/hugegraph-pd/apache-hugegraph-pd-1.7.0
ST=$H/hugegraph/hugegraph-store/apache-hugegraph-store-1.7.0
SVH=$H/hg-master/hugegraph-server/dist-master-hstore
SVB=$H/hg-master/hugegraph-server/dist-master-hstore-B
RD=$H/hg-master/hugegraph-server/dist-master-rocksdb
J17="export JAVA_HOME=$H/tools/jdk17 PATH=$H/tools/jdk17/bin:/usr/bin:/bin:/usr/sbin"
J11="export JAVA_HOME=$H/tools/jdk11 PATH=$H/tools/jdk11/bin:/usr/bin:/bin:/usr/sbin LC_ALL=C.UTF-8 LANG=C.UTF-8"
pk() { echo "for p in $*; do P=\$(ss -tlnp 2>/dev/null | grep \":\$p \" | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2); [ -n \"\$P\" ] && kill \$P; done"; }
echo "== $(date +%T) stop servers (.235) — NO wipe"
$SSH $N1 "for D in $SVH $SVB $RD; do (cd \$D && ./bin/stop-hugegraph.sh) >/dev/null 2>&1; rm -f \$D/bin/pid; done; sleep 3; $(pk 8080 8082 8081 8182 8184 8183); echo servers-stopped"
echo "== $(date +%T) stop stores (.235 .236 .237) — NO wipe"
for N in $N1 $N2 $N3; do $SSH $N "(cd $ST && ./bin/stop-hugegraph-store.sh) >/dev/null 2>&1; sleep 2; $(pk 8500); rm -f $ST/bin/pid; echo \"store stopped \$(hostname)\""; done
echo "== $(date +%T) stop PD (.235) — NO wipe"
$SSH $N1 "(cd $PD && ./bin/stop-hugegraph-pd.sh) >/dev/null 2>&1; sleep 2; $(pk 8686 8620); rm -f $PD/bin/pid; echo pd-stopped"
sleep 3
echo "== $(date +%T) start PD"
$SSH $N1 "$J17; (cd $PD && ./bin/start-hugegraph-pd.sh) >/dev/null 2>&1; for i in \$(seq 1 20); do ss -tln | grep -q ':8686 ' && break; sleep 2; done; ss -tln | grep -q ':8686 ' && echo pd-up || echo PD-DOWN"
sleep 12
echo "== $(date +%T) start stores"
for N in $N1 $N2 $N3; do $SSH $N "$J17; (cd $ST && ./bin/start-hugegraph-store.sh) >/dev/null 2>&1; for i in \$(seq 1 30); do ss -tln | grep -q ':8500 ' && break; sleep 2; done; ss -tln | grep -q ':8500 ' && echo \"store-up \$(hostname)\" || echo \"STORE-DOWN \$(hostname)\""; done
sleep 5
echo "== $(date +%T) start servers (A 8080, B 8082, rocksdb 8081)"
$SSH $N1 "$J11; for D in $SVH $SVB $RD; do rm -f \$D/bin/pid; (cd \$D && ./bin/start-hugegraph.sh) >/dev/null 2>&1; done; for i in \$(seq 1 40); do curl -sf http://127.0.0.1:8080/graphspaces/DEFAULT/graphs >/dev/null && curl -sf http://127.0.0.1:8082/graphspaces/DEFAULT/graphs >/dev/null && break; sleep 3; done; echo \"A: \$(curl -s --compressed http://127.0.0.1:8080/graphspaces/DEFAULT/graphs | head -c 200)\""
echo "== $(date +%T) restart done"
