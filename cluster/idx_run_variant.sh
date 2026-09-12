#!/usr/bin/env bash
# run_variant.sh <results-tag> <variant> <edges> [bench-args...] — wipe + boot the lab cluster, load with ~/idx_bench.py
# <variant> <edges> 4 500 100000, then: 60 s settle, serialised manual compaction of every partition, disk split
# (db / raft log / snapshot), edge count. Results in ~/idxbench/<tag>/ on the nodes, copied to results/index-cost/<tag>/.
set -u
R=$1; V=$2; E=$3; shift 3; EXTRA="$*"
S=$HOME/hugegraph-validation
SSH="ssh -n -o ConnectTimeout=15 -o BatchMode=yes"; N1=seba@192.168.80.235; N2=seba@192.168.80.236; N3=seba@192.168.80.237
H=/home/seba; PD=$H/hugegraph/hugegraph-pd/apache-hugegraph-pd-1.7.0
J17="export JAVA_HOME=$H/tools/jdk17 PATH=$H/tools/jdk17/bin:/usr/bin:/bin:/usr/sbin"
DISTS="HG_SV=$H/hg-master/hugegraph-server/dist-master-hstore HG_RD=$H/hg-master/hugegraph-server/dist-master-rocksdb"
for N in 235 236 237; do scp -q $S/cluster/idx_disk_split.sh $S/cluster/sample_res.sh $S/cluster/idx_bench.py seba@192.168.80.$N:~/ ; done
echo "== $(date +%T) [$R/$V] wipe + boot cluster"
$SSH $N1 "$DISTS bash $H/node_servers.sh stop" >/dev/null; for N in $N1 $N2 $N3; do $SSH $N "bash $H/node_store.sh stop" >/dev/null; done
$SSH $N1 "$J17; (cd $PD && ./bin/start-hugegraph-pd.sh) >/dev/null 2>&1; for i in \$(seq 1 20); do ss -tln | grep -q ':8686 ' && break; sleep 2; done; ss -tln | grep -q ':8686 ' || echo '  PD NOT UP'"; sleep 12
for N in $N1 $N2 $N3; do $SSH $N "bash $H/node_store.sh start" | grep -v listening; done
$SSH $N1 "$DISTS bash $H/node_servers.sh start $V" | grep -E "smoke" | cut -c1-40
for N in $N1 $N2 $N3; do $SSH $N "mkdir -p ~/idxbench/$R; [ -f ~/idxbench/sampler.pid ] && kill \$(cat ~/idxbench/sampler.pid) 2>/dev/null; rm -f ~/idxbench/$R/${V}_*; setsid nohup bash ~/sample_res.sh ~/idxbench/$R/${V}_\$(hostname).csv 10 >/dev/null 2>&1 < /dev/null & echo \$! > ~/idxbench/sampler.pid; sleep 1; kill -0 \$(cat ~/idxbench/sampler.pid) && echo \"  sampler on \$(hostname)\""; done; sleep 12
echo "== $(date +%T) [$R/$V] partitions per store: $(for N in 235 236 237; do curl -s -m 10 http://192.168.80.$N:8520/v1/partitions | python3 -c 'import sys,json; d=json.load(sys.stdin); g=d["partitions"]; print("%d groups (leader %d, peers %s)" % (len(g), sum(1 for x in g if x["role"]=="STATE_LEADER"), sorted(set(len(x["conf"].split(",")) for x in g))), end="; ")' 2>&1 | tail -c 80; done)"
echo "== $(date +%T) [$R/$V] load 100k vertices + $E edges $EXTRA"
$SSH $N1 "python3 -u ~/idx_bench.py $V $E 4 500 100000 ~/idxbench/$R/$V.json $EXTRA 2>&1 | grep -E 'RESULT|Traceback|Error|edges [0-9]+/' | awk '!/edges [0-9]+/ || NR%40==0' | cut -c1-230"
echo "== $(date +%T) [$R/$V] settle 60 s, then compaction + disk split"; sleep 60
for N in $N1 $N2 $N3; do $SSH $N "kill \$(cat ~/idxbench/sampler.pid) 2>/dev/null; bash ~/idx_disk_split.sh compact > ~/idxbench/$R/${V}_disk_\$(hostname).txt 2>&1; cat ~/idxbench/$R/${V}_disk_\$(hostname).txt"; done
$SSH $N1 "curl -s --compressed -X POST http://127.0.0.1:8080/gremlin -H 'Content-Type: application/json' -d '{\"gremlin\":\"g.E().count()\",\"aliases\":{\"graph\":\"DEFAULT-hugegraph\",\"g\":\"__g_DEFAULT-hugegraph\"}}' | grep -o '\"data\":\[[^]]*\]'"
mkdir -p $S/results/index-cost/$R; for N in $N1 $N2 $N3; do scp -q $N:"~/idxbench/$R/${V}*" $S/results/index-cost/$R/ 2>/dev/null; done
echo "== $(date +%T) [$R/$V] done"
