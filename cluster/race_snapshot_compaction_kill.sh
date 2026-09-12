#!/usr/bin/env bash
# race_kill.sh <partition-id> [gap-ms=300] — as race.sh, but records the partition's raft snapshot dir right after the
# snapshot request returns (while the compaction is still running), then kills the store (SIGKILL, as a crash during
# the compaction would), restarts it without wiping, and reports how the partition comes back.
P=$1; GAP=${2:-300}; ST=$HOME/hugegraph/hugegraph-store/apache-hugegraph-store-1.7.0; L=$ST/logs/hugegraph-store-server.log
PD=$(printf "%05d" $P); SD=$ST/storage/raft/$PD/snapshot
list() { for d in $SD/*/; do echo "    $(basename $d): meta=$([ -f $d/__raft_snapshot_meta ] && echo yes || echo NO) data_dir=$([ -d $d/data ] && echo "yes($(ls $d/data | wc -l) files)" || echo NO) should_not_load=$([ -f $d/should_not_load ] && echo yes || echo no)"; done; }
echo "partition $P: db=$(du -sm $ST/storage/db/$PD | cut -f1) MB, snapshots before:"; list
LN=$(wc -l < $L); T0=$(date +%s.%N)
curl -s -m 600 -X POST "http://127.0.0.1:8520/v1/compat?id=$P" > /dev/null &
sleep $(LC_ALL=C awk -v g=$GAP 'BEGIN{printf "%.3f", g/1000}')
curl -s -m 60 "http://127.0.0.1:8520/test/snapshot" > /dev/null; T1=$(date +%s.%N)
sleep 1
echo "snapshot dirs $(LC_ALL=C awk -v a=$T0 -v b=$T1 'BEGIN{printf "%.1f", b-a+1}') s after the compaction started (compaction still running):"; list
SP=$(pgrep -f "Dname=HugeGraphStore" | head -1); kill -9 $SP; T2=$(date +%s.%N); echo "store pid $SP killed at +$(LC_ALL=C awk -v a=$T0 -v b=$T2 'BEGIN{printf "%.1f", b-a}') s"
sleep 2; echo "snapshot dirs after the kill:"; list
export JAVA_HOME=$HOME/tools/jdk17 PATH=$HOME/tools/jdk17/bin:/usr/bin:/bin:/usr/sbin
rm -f $ST/bin/pid; (cd $ST && ./bin/start-hugegraph-store.sh) > /dev/null 2>&1
for i in $(seq 1 30); do ss -tln | grep -q ':8500 ' && break; sleep 2; done; echo "store restarted (port 8500 $(ss -tln | grep -q ':8500 ' && echo up || echo DOWN))"
sleep 45
echo "-- store log for partition $P after the restart:"; tail -n +$((LN+1)) $L | grep -E "Raft $P |Raft $P,|Partition $P[ -]|partition $P[ -]|/$PD/" | grep -iv "heartbeat" | head -20 | cut -c1-170
echo "-- partition $P state from the store REST:"; curl -s -m 10 "http://127.0.0.1:8520/v1/partition/$P" | python3 -c "import sys,json; d=json.load(sys.stdin); print([(p['id'],p['workState'],p['leader'] if 'leader' in p else '') for p in d['partitions']])" 2>&1 | tail -1
echo "-- errors in the log after the restart:"; tail -n +$((LN+1)) $L | grep -c "\[ERROR\]"; tail -n +$((LN+1)) $L | grep "\[ERROR\]" | head -5 | cut -c1-170
