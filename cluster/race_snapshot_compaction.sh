#!/usr/bin/env bash
# race.sh <partition-id> [gap-ms=300] — apache/hugegraph#3162 on the lab store at 192.168.80.235 (run on that node):
# start a blocking full compaction of one partition through the store REST, <gap> ms later trigger a raft snapshot of
# every partition (/test/snapshot), then report what the partition's raft snapshot directory contains.
P=$1; GAP=${2:-300}; ST=$HOME/hugegraph/hugegraph-store/apache-hugegraph-store-1.7.0; L=$ST/logs/hugegraph-store-server.log
PD=$(printf "%05d" $P); SD=$ST/storage/raft/$PD/snapshot
echo "partition $P: db=$(du -sm $ST/storage/db/$PD | cut -f1) MB, snapshots before: $(ls $SD 2>/dev/null | tr '\n' ' ')"
LN=$(wc -l < $L)
T0=$(date +%s.%N)
curl -s -m 600 -X POST "http://127.0.0.1:8520/v1/compat?id=$P" > /tmp/race_compact.out &
CPID=$!
sleep $(LC_ALL=C awk -v g=$GAP 'BEGIN{printf "%.3f", g/1000}')
T1=$(date +%s.%N); curl -s -m 60 "http://127.0.0.1:8520/test/snapshot" > /tmp/race_snap.out; T2=$(date +%s.%N)
wait $CPID; T3=$(date +%s.%N)
LC_ALL=C awk -v a=$T0 -v b=$T1 -v c=$T2 -v d=$T3 'BEGIN{printf "compaction submitted at +0, snapshot requested at +%.2fs (returned +%.2fs), compaction REST returned at +%.2fs: ", b-a, c-a, d-a}'; cat /tmp/race_compact.out; echo
sleep 15
echo "-- store log for partition $P since the experiment:"; tail -n +$((LN+1)) $L | grep -E "Partition $P[ -]|Raft $P |Raft $P$" | grep -i "compaction\|snapshot" | cut -c1-150
echo "-- snapshot dirs now:"; for d in $SD/*/; do echo "  $(basename $d): meta=$([ -f $d/__raft_snapshot_meta ] && echo yes || echo NO) data_dir=$([ -d $d/data ] && echo "yes($(ls $d/data | wc -l) files)" || echo NO) should_not_load=$([ -f $d/should_not_load ] && echo yes || echo no)"; done
