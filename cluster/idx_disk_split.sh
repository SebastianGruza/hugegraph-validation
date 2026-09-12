#!/usr/bin/env bash
# disk_split.sh [compact] — per store node: serialised manual compaction of every partition (flush + compactRange via the
# store REST, one at a time because the store allows a single task), then sizes of db/ (data), raft/*/log, raft/*/snapshot,
# SST count, and the logical bytes ingested into RocksDB ("Cumulative writes ... ingest" of every partition LOG).
ST=$HOME/hugegraph/hugegraph-store/apache-hugegraph-store-1.7.0/storage
if [ "$1" = compact ]; then
  t0=$(date +%s); n=0
  for d in $ST/db/*/; do id=$((10#$(basename $d)))
    for i in $(seq 1 300); do
      r=$(curl -s -m 30 -X POST "http://127.0.0.1:8520/v1/compat?id=$id"); echo "$r" | grep -q '"OK"' && { n=$((n+1)); break; }; sleep 1
    done
  done
  for i in $(seq 1 300); do curl -s -m 30 -X POST "http://127.0.0.1:8520/v1/compat?id=$id" | grep -q '"OK"' && break; sleep 1; done  # wait for the last one
  echo "compacted=$n/$(ls -d $ST/db/*/ | wc -l) in $(( $(date +%s)-t0 )) s"
fi
ingest=$(for l in $ST/db/*/LOG; do grep -h "Cumulative writes" $l | tail -1; done | LC_ALL=C awk '{for(i=1;i<=NF;i++) if($i=="ingest:"){gsub(",",".",$(i+1)); gb+=$(i+1)}} END{printf "%.2f\n", gb+0}')
keys=$(for l in $ST/db/*/LOG; do grep -h "Cumulative writes" $l | tail -1; done | LC_ALL=C awk '{k=$5; m=1; if(k~/K$/){m=1000} if(k~/M$/){m=1000000} gsub(/[KM]/,"",k); s+=k*m} END{printf "%d", s}')
echo "$(hostname): db_MB=$(du -sm $ST/db|cut -f1) raftlog_MB=$(du -scm $ST/raft/*/log|tail -1|cut -f1) snapshot_MB=$(du -scm $ST/raft/*/snapshot|tail -1|cut -f1) sst=$(find $ST/db -name '*.sst'|wc -l) parts=$(ls -d $ST/db/*/|wc -l) ingest_GB=$ingest keys=$keys"
# per-table key counts and data sizes of the partitions hosted on this node (store REST, unauthenticated)
{ curl -s -m 30 http://127.0.0.1:8520/v1/partitions | grep -q '"partitions"' && curl -s -m 30 http://127.0.0.1:8520/v1/partitions || { echo '{"partitions":['; sep=""; for d in $ST/db/*/; do id=$((10#$(basename $d))); r=$(curl -s -m 10 "http://127.0.0.1:8520/v1/partition/$id"); echo "$r" | grep -q '"partitions"' && { echo "$sep$r"; sep=","; }; done; echo ']}'; }; } | python3 -c '
import sys, json, re, collections
d = json.load(sys.stdin); keys = collections.Counter(); size = collections.Counter(); n = 0
def mb(s):
    v, u = s.split(); v = float(v); return v * {"bytes": 1/2**20, "KB": 1/1024, "MB": 1, "GB": 1024}[u]
for grp in d["partitions"]:
    for p in grp["partitions"]:
        n += 1
        for t in p["metric"]["tables"]:
            keys[t["tableName"]] += t["keyCount"]; size[t["tableName"]] += mb(t["dataSize"])
print("tables partitions=%d " % n + " ".join("%s:keys=%d,MB=%.0f" % (t, keys[t], size[t]) for t in sorted(keys)))
' 2>&1 | tail -1
