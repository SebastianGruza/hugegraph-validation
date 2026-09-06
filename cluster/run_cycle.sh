#!/usr/bin/env bash
# run_cycle.sh <master|fix|combined> — orkiestracja z kolektora: pelny cykl na klastrze 3-store (.235/.236/.237)
#   stop + wipe: serwery (8080 hstore, 8081 rocksdb) i PD na .235, store'y (node_store.sh) na .235/.236/.237
#   -> jar hstore wg <side> -> PD -> 3 store'y -> init + serwery -> hg_suite.py --load na obu -> compare
# Wyniki na .235: ~/validation/<side>_oracle.{txt,json}, <side>_hstore.{txt,json}, compare_<side>.txt
set -uo pipefail
SIDE=${1:?master|fix|combined}
N1=seba@192.168.80.235; N2=seba@192.168.80.236; N3=seba@192.168.80.237
SSH="ssh -n -o ConnectTimeout=15 -o BatchMode=yes"
H=/home/seba
PD=$H/hugegraph/hugegraph-pd/apache-hugegraph-pd-1.7.0
SV=$H/hugegraph/hugegraph-server/apache-hugegraph-server-1.7.0
RD=$H/hugegraph/hugegraph-server/dist-rocksdb
J17="export JAVA_HOME=$H/tools/jdk17 PATH=$H/tools/jdk17/bin:/usr/bin:/bin:/usr/sbin"
J11="export JAVA_HOME=$H/tools/jdk11 PATH=$H/tools/jdk11/bin:/usr/bin:/bin:/usr/sbin"
OUT=$H/validation
ts() { date +%T; }

# ktory serwer: combined = dystrybucje zbudowane z galezi combined; master = dystrybucje zbudowane z master 98477f0
# (~/hg-master worktree, dist-master-hstore / dist-master-rocksdb); fix = combined + jar hstore z PR #3184 (historyczne)
case $SIDE in
  combined) DISTS="" ;;
  master)   DISTS="HG_SV=$H/hg-master/hugegraph-server/dist-master-hstore HG_RD=$H/hg-master/hugegraph-server/dist-master-rocksdb" ;;
  fix)      DISTS=""; $SSH $N1 "cp /tmp/hstore-fix.jar $SV/lib/hugegraph-hstore-1.7.0.jar" ;;
  *) echo "unknown side $SIDE"; exit 2 ;;
esac
echo "== [$SIDE] server dists: ${DISTS:-combined build}"
echo "== [$SIDE] $(ts) stop servers + pd on .235"
$SSH $N1 "$DISTS bash $H/node_servers.sh stop"
echo "== [$SIDE] $(ts) stop + wipe stores on .235 .236 .237"
for N in $N1 $N2 $N3; do $SSH $N "bash $H/node_store.sh stop"; done
echo "== [$SIDE] $(ts) start PD on .235"
$SSH $N1 "$J17; (cd $PD && ./bin/start-hugegraph-pd.sh) >/dev/null 2>&1; for i in \$(seq 1 20); do ss -tln | grep -q ':8686 ' && break; sleep 2; done; ss -tln | grep -q ':8686 ' && echo '  pd listening' || echo '  PD NOT UP'"
sleep 12   # lider raft PD; store'y odbite w tym oknie wyczerpuja retry i gasna
echo "== [$SIDE] $(ts) start stores on .235 .236 .237"
for N in $N1 $N2 $N3; do $SSH $N "bash $H/node_store.sh start"; done
echo "== [$SIDE] $(ts) init + servers on .235 (jdk11)"
$SSH $N1 "$DISTS bash $H/node_servers.sh start $SIDE"
echo "== [$SIDE] $(ts) suite --load on oracle (8081)"
$SSH $N1 "python3 -u $H/hg_suite.py run --port 8081 --load --out $OUT/${SIDE}_oracle.json > $OUT/${SIDE}_oracle.txt 2>&1; tail -2 $OUT/${SIDE}_oracle.txt"
echo "== [$SIDE] $(ts) suite --load on hstore (8080)"
$SSH $N1 "python3 -u $H/hg_suite.py run --port 8080 --load --out $OUT/${SIDE}_hstore.json --expect $OUT/${SIDE}_oracle.json > $OUT/${SIDE}_hstore.txt 2>&1; tail -3 $OUT/${SIDE}_hstore.txt; python3 $H/hg_suite.py compare $OUT/${SIDE}_oracle.json $OUT/${SIDE}_hstore.json --ids > $OUT/compare_${SIDE}.txt 2>&1; head -8 $OUT/compare_${SIDE}.txt"
echo "== [$SIDE] $(ts) DONE"
