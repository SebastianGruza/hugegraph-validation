#!/usr/bin/env bash
# rep3_kill.sh <jar: master|pr> <tag> — on the already loaded replication-3 cluster: swap the store jar on all nodes
# without wiping (graceful stop + start), run race_kill.sh on a partition led by .235 (race, SIGKILL the store during
# the compaction, restart without wiping), then report: cluster-level edge count through the server, the partition's
# state on every node, and finally the operator recovery "wipe raft+db of that partition on the broken node only".
set -u
JAR=$1; TAG=$2
SSH="ssh -n -o ConnectTimeout=15 -o BatchMode=yes"; N1=seba@192.168.80.235; N2=seba@192.168.80.236; N3=seba@192.168.80.237
H=/home/seba; ST=$H/hugegraph/hugegraph-store/apache-hugegraph-store-1.7.0
RESTART='D=~/hugegraph/hugegraph-store/apache-hugegraph-store-1.7.0; (cd $D && ./bin/stop-hugegraph-store.sh) >/dev/null 2>&1; sleep 4; P=$(ss -tlnp 2>/dev/null | grep ":8500 " | grep -o "pid=[0-9]*" | head -1 | cut -d= -f2); [ -n "$P" ] && kill -9 $P; sleep 2; bash ~/node_store.sh start | tail -1; echo "  $(hostname) jar=$(md5sum $D/lib/hg-store-node-1.7.0.jar | cut -c1-8)"'
count() { $SSH $N1 "curl -s -m 300 --compressed -X POST http://127.0.0.1:8080/gremlin -H 'Content-Type: application/json' -d '{\"gremlin\":\"g.E().count()\",\"aliases\":{\"graph\":\"DEFAULT-hugegraph\",\"g\":\"__g_DEFAULT-hugegraph\"}}' | grep -o '\"data\":\[[^]]*\]\|\"message\":\"[^\"]\{0,80\}'"; }
pstate() { for N in 235 236 237; do echo "  $N: $(curl -s -m 10 http://192.168.80.$N:8520/v1/partition/$1 | python3 -c 'import sys,json; d=json.load(sys.stdin); print([(p["id"],p["workState"]) for g in [d] for p in g.get("partitions",[])], d.get("role"), "conf=" + str(d.get("conf",""))[:60])' 2>/dev/null || echo 'partition REST 500/unavailable')"; done; }
echo "== $(date +%T) [$TAG] ensure jar $JAR on all nodes (no wipe)"
for N in $N1 $N2 $N3; do
  if $SSH $N "cmp -s $ST/lib/hg-store-node-1.7.0.jar.$JAR $ST/lib/hg-store-node-1.7.0.jar && ss -tln | grep -q ':8500 '"; then
    $SSH $N 'echo "  $(hostname): already on the requested jar and up"'
  else
    $SSH $N "cp $ST/lib/hg-store-node-1.7.0.jar.$JAR $ST/lib/hg-store-node-1.7.0.jar"; $SSH $N "$RESTART"
  fi
done; sleep 45
echo "== $(date +%T) [$TAG] edge count before: $(count)"
P=$($SSH $N1 "for d in $ST/storage/db/*/; do id=\$((10#\$(basename \$d))); r=\$(curl -s -m 5 http://127.0.0.1:8520/v1/partition/\$id); echo \"\$r\" | grep -q '\"role\":\"STATE_LEADER\"' || continue; echo \"\$(du -sm $ST/storage/raft/\$(basename \$d) | cut -f1) \$id\"; done | sort -rn | head -1 | awk '{print \$2}'"); echo "  partition led by .235: $P"; [ -n "$P" ] || { echo "no leader on .235"; exit 1; }
echo "== $(date +%T) [$TAG] race + SIGKILL + restart on .235, partition $P"; $SSH $N1 "bash ~/race_kill.sh $P 300 ${KILL_AFTER:-1} 2>&1 | cut -c1-200"
echo "== $(date +%T) [$TAG] partition $P state on every node"; pstate $P
echo "== $(date +%T) [$TAG] edge count with .235's replica broken: $(count)"
for N in $N2 $N3; do $SSH $N "echo \"-- \$(hostname) raft log for hg_$P (last 5):\"; grep -E 'hg_$P/' $ST/logs/raft-hugegraph-store.log | grep -iE 'leader|snapshot|install|error|warn' | tail -5 | cut -c1-190"; done
echo "== $(date +%T) [$TAG] recovery: wipe raft/$(printf %05d $P) AND db/$(printf %05d $P) on .235 only, restart"; $SSH $N1 "D=$ST; (cd \$D && ./bin/stop-hugegraph-store.sh) >/dev/null 2>&1; sleep 4; Q=\$(ss -tlnp 2>/dev/null | grep ':8500 ' | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2); [ -n \"\$Q\" ] && kill -9 \$Q; sleep 2; rm -rf \$D/storage/raft/$(printf %05d $P) \$D/storage/db/$(printf %05d $P); LN=\$(wc -l < \$D/logs/hugegraph-store-server.log); RN=\$(wc -l < \$D/logs/raft-hugegraph-store.log); export JAVA_HOME=\$HOME/tools/jdk17 PATH=\$HOME/tools/jdk17/bin:/usr/bin:/bin:/usr/sbin; rm -f \$D/bin/pid; (cd \$D && ./bin/start-hugegraph-store.sh) >/dev/null 2>&1; for i in \$(seq 1 30); do ss -tln | grep -q ':8500 ' && break; sleep 2; done; sleep 60; echo '-- store log for partition $P after the wipe+restart:'; tail -n +\$((LN+1)) \$D/logs/hugegraph-store-server.log | grep -E 'Raft $P |Partition $P[ -]|g-$P |snapshot' | grep -iv heartbeat | head -8 | cut -c1-180; echo '-- raft log:'; tail -n +\$((RN+1)) \$D/logs/raft-hugegraph-store.log | grep -E 'hg_$P/' | grep -iE 'install|snapshot|init|follow|error' | head -6 | cut -c1-190; echo \"-- db/$(printf %05d $P) now: \$(du -sm \$D/storage/db/$(printf %05d $P) 2>/dev/null | cut -f1) MB\""
echo "== $(date +%T) [$TAG] partition $P state after recovery"; pstate $P
echo "== $(date +%T) [$TAG] edge count after recovery: $(count)"
echo "== $(date +%T) [$TAG] done"
