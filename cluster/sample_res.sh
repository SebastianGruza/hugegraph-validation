#!/usr/bin/env bash
# sample_res.sh <out.csv> [interval=5] — every interval: epoch, store pid rss_kb, store cpu ticks, server rss_kb, server cpu ticks,
# du_kb of the store storage dir, system idle ticks. Runs until killed.
OUT=$1; IV=${2:-5}; ST=$HOME/hugegraph/hugegraph-store/apache-hugegraph-store-1.7.0/storage
echo "epoch,store_rss_kb,store_cpu_ticks,server_rss_kb,server_cpu_ticks,storage_kb,sys_idle_ticks,sys_total_ticks" > "$OUT"
while true; do
  SP=$(pgrep -f "Dname=HugeGraphStore" | head -1); VP=$(pgrep -f "HugeGraphServer" | head -1)
  s_rss=0; s_cpu=0; v_rss=0; v_cpu=0
  [ -n "$SP" ] && { s_rss=$(awk '/VmRSS/{print $2}' /proc/$SP/status 2>/dev/null); s_cpu=$(awk '{print $14+$15}' /proc/$SP/stat 2>/dev/null); }
  [ -n "$VP" ] && { v_rss=$(awk '/VmRSS/{print $2}' /proc/$VP/status 2>/dev/null); v_cpu=$(awk '{print $14+$15}' /proc/$VP/stat 2>/dev/null); }
  du_kb=$(du -sk "$ST" 2>/dev/null | cut -f1); read -r _ u n s idle rest < /proc/stat; tot=$((u+n+s+idle))
  echo "$(date +%s),${s_rss:-0},${s_cpu:-0},${v_rss:-0},${v_cpu:-0},${du_kb:-0},$idle,$tot" >> "$OUT"; sleep "$IV"
done
