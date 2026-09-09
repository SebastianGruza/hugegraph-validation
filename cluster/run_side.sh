#!/usr/bin/env bash
# run_side.sh <tag> [sections] — like run_cycle.sh, but the server dists come from ~/hg-<tag>/hugegraph-server/dist-<tag>-{hstore,rocksdb}
#   full cycle: stop+wipe servers/PD/stores -> PD -> 3 stores -> init + servers -> hg_suite.py --load on both -> compare
#   results on .235: ~/validation/<tag>_oracle.{txt,json}, <tag>_hstore.{txt,json}, compare_<tag>.txt
set -uo pipefail
TAG=${1:?tag}; SECTIONS=${2:-S,L,J,K}
N1=seba@192.168.80.235; N2=seba@192.168.80.236; N3=seba@192.168.80.237
SSH="ssh -n -o ConnectTimeout=15 -o BatchMode=yes"
H=/home/seba
PD=$H/hugegraph/hugegraph-pd/apache-hugegraph-pd-1.7.0
J17="export JAVA_HOME=$H/tools/jdk17 PATH=$H/tools/jdk17/bin:/usr/bin:/bin:/usr/sbin"
OUT=$H/validation
DISTS="HG_SV=$H/hg-$TAG/hugegraph-server/dist-$TAG-hstore HG_RD=$H/hg-$TAG/hugegraph-server/dist-$TAG-rocksdb"
ts() { date +%T; }
$SSH $N1 "test -d $H/hg-$TAG/hugegraph-server/dist-$TAG-hstore/lib" || { echo "no dist for $TAG"; exit 2; }
# guard: both dists of a tag must carry the same hugegraph-core jar (a half-finished make_dists.sh leaves a stale rocksdb dist)
$SSH $N1 "A=\$(md5sum $H/hg-$TAG/hugegraph-server/dist-$TAG-hstore/lib/hugegraph-core-1.7.0.jar | cut -c1-8); B=\$(md5sum $H/hg-$TAG/hugegraph-server/dist-$TAG-rocksdb/lib/hugegraph-core-1.7.0.jar | cut -c1-8); echo \"== [$TAG] core jar hstore=\$A rocksdb=\$B\"; [ \"\$A\" = \"\$B\" ]" || { echo "core jar mismatch between the hstore and rocksdb dists of $TAG, refusing to run"; exit 3; }
echo "== [$TAG] $(ts) stop servers + pd on .235"
$SSH $N1 "$DISTS bash $H/node_servers.sh stop"
echo "== [$TAG] $(ts) stop + wipe stores"
for N in $N1 $N2 $N3; do $SSH $N "bash $H/node_store.sh stop"; done
echo "== [$TAG] $(ts) start PD"
$SSH $N1 "$J17; (cd $PD && ./bin/start-hugegraph-pd.sh) >/dev/null 2>&1; for i in \$(seq 1 20); do ss -tln | grep -q ':8686 ' && break; sleep 2; done; ss -tln | grep -q ':8686 ' && echo '  pd listening' || echo '  PD NOT UP'"
sleep 12
echo "== [$TAG] $(ts) start stores"
for N in $N1 $N2 $N3; do $SSH $N "bash $H/node_store.sh start"; done
echo "== [$TAG] $(ts) init + servers (jdk11)"
$SSH $N1 "$DISTS bash $H/node_servers.sh start $TAG"
echo "== [$TAG] $(ts) suite --load on oracle (8081) sections=$SECTIONS"
$SSH $N1 "python3 -u $H/hg_suite.py run --port 8081 --load --sections $SECTIONS --out $OUT/${TAG}_oracle.json > $OUT/${TAG}_oracle.txt 2>&1; tail -2 $OUT/${TAG}_oracle.txt"
echo "== [$TAG] $(ts) suite --load on hstore (8080)"
$SSH $N1 "python3 -u $H/hg_suite.py run --port 8080 --load --sections $SECTIONS --out $OUT/${TAG}_hstore.json --expect $OUT/${TAG}_oracle.json > $OUT/${TAG}_hstore.txt 2>&1; tail -3 $OUT/${TAG}_hstore.txt; python3 $H/hg_suite.py compare $OUT/${TAG}_oracle.json $OUT/${TAG}_hstore.json --ids > $OUT/compare_${TAG}.txt 2>&1; head -8 $OUT/compare_${TAG}.txt"
echo "== [$TAG] $(ts) hg_j8 on oracle + hstore"
$SSH $N1 "python3 -u $H/hg_j8.py run --port 8081 --out $OUT/j8_${TAG}_oracle.json > $OUT/j8_${TAG}_oracle.txt 2>&1; tail -1 $OUT/j8_${TAG}_oracle.txt; python3 -u $H/hg_j8.py run --port 8080 --out $OUT/j8_${TAG}_hstore.json --expect $OUT/j8_${TAG}_oracle.json > $OUT/j8_${TAG}_hstore.txt 2>&1; tail -2 $OUT/j8_${TAG}_hstore.txt"
echo "== [$TAG] $(ts) DONE"
