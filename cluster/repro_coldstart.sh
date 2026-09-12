#!/usr/bin/env bash
# repro_coldstart.sh <store-delay-s> <init|server> [tag] — apache/hugegraph#3203 reproduction on the 3-node lab.
# Wipes PD + stores + server, starts PD, starts the server-side step immediately (init-store.sh, or start-hugegraph.sh
# without init-store), and starts the 3 stores <delay> seconds after PD is listening. Prints one summary line with the
# store registration times (PD log "from Offline to Up"), the first/last "error code = 105", the retry sleeps, the
# "retries reached the upper limit" time, the step's exit code and duration, and whether REST came up.
set -u
X=$1; MODE=$2; TAG=${3:-x${X}-${MODE}}; X2=${X2:-$X}   # X2: delay of the 2nd/3rd store (env)
SSH="ssh -n -o ConnectTimeout=15 -o BatchMode=yes"; N1=seba@192.168.80.235; N2=seba@192.168.80.236; N3=seba@192.168.80.237
H=/home/seba; PD=$H/hugegraph/hugegraph-pd/apache-hugegraph-pd-1.7.0; SV=$H/hg-master/hugegraph-server/dist-master-hstore
J17="export JAVA_HOME=$H/tools/jdk17 PATH=$H/tools/jdk17/bin:/usr/bin:/bin:/usr/sbin"; J11="export JAVA_HOME=$H/tools/jdk11 PATH=$H/tools/jdk11/bin:/usr/bin:/bin:/usr/sbin"
DISTS="HG_SV=$SV HG_RD=$H/hg-master/hugegraph-server/dist-master-rocksdb"
echo "== $(date +%T) [$TAG] wipe"
$SSH $N1 "$DISTS bash $H/node_servers.sh stop" >/dev/null; for N in $N1 $N2 $N3; do $SSH $N "bash $H/node_store.sh stop" >/dev/null; done
$SSH $N1 "$J17; (cd $PD && ./bin/start-hugegraph-pd.sh) >/dev/null 2>&1; for i in \$(seq 1 30); do ss -tln | grep -q ':8686 ' && break; sleep 1; done; date +%s.%N > /tmp/repro_tpd; ss -tln | grep -q ':8686 ' || echo '  PD NOT UP'"
TPD=$($SSH $N1 "cat /tmp/repro_tpd"); echo "== $(date +%T) [$TAG] PD listening; stores start in $X s; server step: $MODE"
for N in $N1 $N2 $N3; do D=$X; [ $N != $N1 ] && D=$X2; $SSH $N "setsid nohup bash -c 'sleep $D; date +%s.%N > /tmp/repro_tstore; bash ~/node_store.sh start' > /tmp/repro_store.log 2>&1 < /dev/null &"; done
if [ "$MODE" = init ]; then
  $SSH $N1 "$J11; cd $SV; T0=\$(date +%s.%N); echo '' | ./bin/init-store.sh > /tmp/repro_step.log 2>&1; RC=\$?; T1=\$(date +%s.%N); echo \"step=init-store exit=\$RC duration=\$(awk -v a=\$T0 -v b=\$T1 'BEGIN{printf \"%.0f\", b-a}') s (started \$(awk -v a=\$T0 -v p=$TPD 'BEGIN{printf \"%.0f\", a-p}') s after PD)\""
else
  $SSH $N1 "$J11; cd $SV; T0=\$(date +%s.%N); ./bin/start-hugegraph.sh -t 600 -j \"-Duser.language=en -Duser.country=US\" > /tmp/repro_step.log 2>&1; RC=\$?; T1=\$(date +%s.%N); echo \"step=start-hugegraph exit=\$RC duration=\$(awk -v a=\$T0 -v b=\$T1 'BEGIN{printf \"%.0f\", b-a}') s (started \$(awk -v a=\$T0 -v p=$TPD 'BEGIN{printf \"%.0f\", a-p}') s after PD)\"; tail -2 /tmp/repro_step.log | cut -c1-160"
fi
# wait until the stores had time to register (X s delay + up to 60 s boot), then summarise
$SSH $N1 "while [ \$(LC_ALL=C awk -v p=$TPD 'BEGIN{print int(systime()-p)}') -lt $(( (X>X2?X:X2)+75 )) ]; do sleep 2; done"
$SSH $N1 "L=$SV/logs/hugegraph-server.log; P=\$(ls $PD/logs/*.log | head -1); echo \"  store Up in PD log: \$(grep -h 'from Offline to Up\|from Unknown to Up' \$P | awk '{print \$2}' | tr '\n' ' ')\"; echo \"  105: count=\$(grep -c 'code = 105' \$L) first=\$(grep -m1 'code = 105' \$L | awk '{print \$2}') last=\$(grep 'code = 105' \$L | tail -1 | awk '{print \$2}') sleeps=\$(grep -o 'Waiting [0-9]* seconds' \$L | awk '{print \$2}' | tr '\n' ',')\"; echo \"  upper limit: \$(grep -m1 'upper limit' \$L | awk '{print \$2}')  other errors: \$(grep -c '\[ERROR\]' \$L) (\$(grep '\[ERROR\]' \$L | grep -v 'code = 105\|upper limit' | head -2 | cut -c1-140 | tr '\n' '|'))\"; echo \"  REST: \$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/graphs)  PD says: \$(grep -h 'Offline to Up\|Not_Ready' \$P | tail -1 | cut -c1-120)\""
for N in $N1 $N2 $N3; do $SSH $N "echo \"  \$(hostname): store start at +\$(awk -v a=\$(cat /tmp/repro_tstore 2>/dev/null || echo 0) -v p=$TPD 'BEGIN{printf \"%.0f\", a-p}') s after PD; \$(grep -h -m1 'Store register\|onStoreStatusChanged' ~/hugegraph/hugegraph-store/apache-hugegraph-store-1.7.0/logs/hugegraph-store-server.log 2>/dev/null | cut -c1-80)\""; done
echo "== $(date +%T) [$TAG] done"
