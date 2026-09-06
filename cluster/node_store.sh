#!/usr/bin/env bash
# node_store.sh stop|start — the hstore store on this node (copied to .235/.236/.237)
#   stop : stop script -> kill by port 8500 PID and by pattern -> wipe storage (including hgstore-metadata with the store ID!)
#   start: start on jdk17, wait until 8500 listens (up to 60 s), 1 retry; print the state
set -uo pipefail
ST=$HOME/hugegraph/hugegraph-store/apache-hugegraph-store-1.7.0
HN=$(hostname)
case ${1:?stop|start} in
  stop)
    (cd "$ST" && ./bin/stop-hugegraph-store.sh) >/dev/null 2>&1
    sleep 2
    for i in $(seq 1 10); do
      P=$(ss -tlnp 2>/dev/null | grep ':8500 ' | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
      [ -n "$P" ] && kill -9 "$P" 2>/dev/null
      for Q in $(pgrep -f 'hg-store-nod[e]') $(pgrep -f 'apache-hugegraph-stor[e]'); do kill -9 "$Q" 2>/dev/null; done
      ss -tln | grep -q ':8500 ' || break
      sleep 2
    done
    rm -rf "$ST/storage" "$ST/bin/pid"
    rm -f "$ST"/logs/*.log
    echo "  [$HN] store stopped, storage wiped ($(ss -tln | grep -q ':8500 ' && echo 'PORT STILL OPEN' || echo 'port free'))"
    ;;
  start)
    export JAVA_HOME=$HOME/tools/jdk17 PATH=$HOME/tools/jdk17/bin:/usr/bin:/bin:/usr/sbin
    for attempt in 1 2; do
      rm -f "$ST/bin/pid"
      (cd "$ST" && ./bin/start-hugegraph-store.sh) >/dev/null 2>&1
      for i in $(seq 1 30); do ss -tln | grep -q ':8500 ' && break; sleep 2; done
      if ss -tln | grep -q ':8500 '; then echo "  [$HN] store listening (attempt $attempt)"; exit 0; fi
      echo "  [$HN] store not up after attempt $attempt: $(grep -h 'does not match\|PD unreachable\|error code' "$ST"/logs/hugegraph-store-server.log 2>/dev/null | tail -1 | cut -c1-160)"
      sleep 5
    done
    echo "  [$HN] STORE FAILED"; exit 1
    ;;
esac
