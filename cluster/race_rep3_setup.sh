#!/usr/bin/env bash
# rep3_race.sh <jar: master|pr> <shards: 1|3> <tag> — wipe/boot the lab cluster with the given store jar and PD
# default-shard-count, load 5 M sk4 edges + 20 M appended (unflushed data), then on the leader of one partition run
# the compaction/snapshot race (race.sh) and report what every replica logged for that partition.
set -u
JAR=$1; SHARDS=$2; TAG=$3
S=/tmp/claude-1000/-home-seba-hugegraph/2ec537c4-ba47-47f1-a5c6-892c978284f0/scratchpad
V=/home/seba/hugegraph-oracle-suite-public
SSH="ssh -n -o ConnectTimeout=15 -o BatchMode=yes"; N1=seba@192.168.80.235; N2=seba@192.168.80.236; N3=seba@192.168.80.237
H=/home/seba; PD=$H/hugegraph/hugegraph-pd/apache-hugegraph-pd-1.7.0; ST=$H/hugegraph/hugegraph-store/apache-hugegraph-store-1.7.0
J17="export JAVA_HOME=$H/tools/jdk17 PATH=$H/tools/jdk17/bin:/usr/bin:/bin:/usr/sbin"
DISTS="HG_SV=$H/hg-master/hugegraph-server/dist-master-hstore HG_RD=$H/hg-master/hugegraph-server/dist-master-rocksdb"
echo "== $(date +%T) [$TAG] jar=$JAR shards=$SHARDS"
for N in $N1 $N2 $N3; do $SSH $N "cp $ST/lib/hg-store-node-1.7.0.jar.$JAR $ST/lib/hg-store-node-1.7.0.jar; echo \"  \$(hostname) jar=\$(md5sum $ST/lib/hg-store-node-1.7.0.jar | cut -c1-8)\""; done
$SSH $N1 "sed -i -E 's/^(\s*default-shard-count:\s*).*/\1$SHARDS/' $PD/conf/application.yml; grep -n 'default-shard-count' $PD/conf/application.yml"
$SSH $N1 "$DISTS bash $H/node_servers.sh stop" >/dev/null; for N in $N1 $N2 $N3; do $SSH $N "bash $H/node_store.sh stop" >/dev/null; done
$SSH $N1 "$J17; (cd $PD && ./bin/start-hugegraph-pd.sh) >/dev/null 2>&1; for i in \$(seq 1 20); do ss -tln | grep -q ':8686 ' && break; sleep 2; done"; sleep 12
for N in $N1 $N2 $N3; do $SSH $N "bash $H/node_store.sh start" | grep -v listening; done
$SSH $N1 "$DISTS bash $H/node_servers.sh start sk4-noindex" | grep -E "8080" | cut -c1-60
echo "== $(date +%T) [$TAG] partitions per store"; for N in 235 236 237; do curl -s -m 10 http://192.168.80.$N:8520/v1/partitions 2>/dev/null | python3 -c 'import sys,json; d=json.load(sys.stdin); g=d["partitions"]; print("  '$N': %d groups, leader of %d, peers %s" % (len(g), sum(1 for x in g if x["role"]=="STATE_LEADER"), sorted(set(len(x["conf"].split(",")) for x in g))))' 2>/dev/null || echo "  $N: /v1/partitions unavailable"; done
echo "== $(date +%T) [$TAG] load 5M + 20M"; $SSH $N1 "python3 -u ~/idx_bench.py sk4-noindex 5000000 4 500 100000 /tmp/l1.json 2>&1 | grep -E 'RESULT|Traceback' | cut -c1-120; python3 -u ~/idx_bench.py sk4-noindex 20000000 4 500 100000 /tmp/l2.json 2>&1 | grep -E 'RESULT|Traceback' | cut -c1-120"
echo "== $(date +%T) [$TAG] pick a partition led by .235 with the most data"; P=$($SSH $N1 "for d in $ST/storage/db/*/; do id=\$((10#\$(basename \$d))); r=\$(curl -s -m 5 http://127.0.0.1:8520/v1/partition/\$id); echo \"\$r\" | grep -q '\"role\":\"STATE_LEADER\"' || continue; echo \"\$(du -sm \$d | cut -f1) \$id\"; done | sort -rn | head -1 | awk '{print \$2}'"); echo "  partition $P"
echo "== $(date +%T) [$TAG] race on partition $P (300 ms gap)"; $SSH $N1 "bash ~/race.sh $P 300 2>&1 | cut -c1-220"
echo "== $(date +%T) [$TAG] what the other replicas logged for partition $P"; for N in $N2 $N3; do $SSH $N "echo \"-- \$(hostname)\"; grep -E \"Raft $P |Raft $P\$|Partition $P[ -]\" $ST/logs/hugegraph-store-server.log | grep -iE 'snapshot|install|EBUSY|corrupt|error' | tail -6 | cut -c1-200; grep -E 'hg_$P/' $ST/logs/raft-hugegraph-store.log | grep -iE 'snapshot|install|error|warn' | tail -6 | cut -c1-200; echo \"   snapshot dirs: \$(for d in $ST/storage/raft/\$(printf %05d $P)/snapshot/*/; do [ -d \$d ] && echo -n \"\$(basename \$d):meta=\$([ -f \$d/__raft_snapshot_meta ] && echo y || echo N),data=\$([ -d \$d/data ] && echo y || echo N) \"; done)\""; done
echo "== $(date +%T) [$TAG] done"
