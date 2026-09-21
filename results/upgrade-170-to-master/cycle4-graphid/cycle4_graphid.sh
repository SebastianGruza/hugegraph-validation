#!/usr/bin/env bash
# Cycle 2: usePD=true. 1.7.0 jars -> write; swap lib to master -> read. hstore only.
set -uo pipefail
N1=seba@192.168.80.235; N2=seba@192.168.80.236; N3=seba@192.168.80.237
SSH="ssh -n -o ConnectTimeout=15 -o BatchMode=yes"
H=/home/seba; R=$H/rel-1.7.0/apache-hugegraph-incubating-1.7.0; M=$H/mst
PD=$R/apache-hugegraph-pd-incubating-1.7.0; ST1=$R/apache-hugegraph-store-incubating-1.7.0; STN=$H/rel-1.7.0/apache-hugegraph-store-incubating-1.7.0
SV=$R/apache-hugegraph-server-incubating-1.7.0; SVR=$R/server-rocksdb-1.7.0
J17="export JAVA_HOME=$H/tools/jdk17 PATH=$H/tools/jdk17/bin:/usr/bin:/bin:/usr/sbin"
J11="export JAVA_HOME=$H/tools/jdk11 PATH=$H/tools/jdk11/bin:/usr/bin:/bin:/usr/sbin LC_ALL=C.UTF-8 LANG=C.UTF-8"
pk() { echo "for p in $*; do P=\$(ss -tlnp 2>/dev/null | grep \":\$p \" | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2); [ -n \"\$P\" ] && kill -9 \$P; done"; }
stop_all() {
  $SSH $N1 "for D in $SV $SVR; do (cd \$D && ./bin/stop-hugegraph.sh) >/dev/null 2>&1; rm -f \$D/bin/pid; done; sleep 2; $(pk 8080 8081 8182 8183); echo servers-stopped"
  for N in $N1 $N2 $N3; do D=$STN; [ $N = $N1 ] && D=$ST1; $SSH $N "(cd $D && ./bin/stop-hugegraph-store.sh) >/dev/null 2>&1; sleep 1; $(pk 8500); rm -f $D/bin/pid; echo \"store stopped \$(hostname)\""; done
  $SSH $N1 "(cd $PD && ./bin/stop-hugegraph-pd.sh) >/dev/null 2>&1; sleep 1; $(pk 8686 8620); rm -f $PD/bin/pid; echo pd-stopped"
}
use_lib() { # $1 = 1.7.0 | master
  if [ "$1" = "1.7.0" ]; then
    $SSH $N1 "for D in $PD $ST1 $SV; do cd \$D && rm -rf lib && cp -r lib-1.7.0 lib; done; echo lib-1.7.0-restored-235"
    for N in $N2 $N3; do $SSH $N "cd $STN && rm -rf lib && cp -r lib-1.7.0 lib && echo \"lib-1.7.0 \$(hostname)\""; done
  else
    $SSH $N1 "cd $PD && rm -rf lib && cp -r $M/apache-hugegraph-pd-1.7.0/lib lib; cd $ST1 && rm -rf lib && cp -r $M/apache-hugegraph-store-1.7.0/lib lib; cd $SV && rm -rf lib && cp -r $M/apache-hugegraph-server-1.7.0/lib lib; echo master-lib-235"
    for N in $N2 $N3; do $SSH $N "cd $STN && rm -rf lib && cp -r $H/mst-store-lib/lib lib && echo \"master-lib \$(hostname)\""; done
  fi
}
start_all() { # $1 tag
  $SSH $N1 "$J17; cd $PD && rm -f logs/*.log; ./bin/start-hugegraph-pd.sh >/dev/null 2>&1; for i in \$(seq 1 20); do ss -tln | grep -q ':8686 ' && break; sleep 2; done; ss -tln | grep -q ':8686 ' && echo pd-up || (echo PD-DOWN; tail -5 logs/*.log)"
  sleep 8
  for N in $N1 $N2 $N3; do D=$STN; [ $N = $N1 ] && D=$ST1; $SSH $N "$J17; cd $D && rm -f logs/*.log; ./bin/start-hugegraph-store.sh >/dev/null 2>&1; for i in \$(seq 1 30); do ss -tln | grep -q ':8500 ' && break; sleep 2; done; ss -tln | grep -q ':8500 ' && echo \"store-up \$(hostname)\" || (echo \"STORE-DOWN \$(hostname)\"; tail -3 logs/*.log)"; done
  sleep 12
  $SSH $N1 "$J11; cd $SV && rm -f logs/*.log bin/pid; if [ '$2' = init ]; then for i in 1 2 3 4 5 6; do echo '' | ./bin/init-store.sh > /tmp/init_$1.log 2>&1; grep -q 'less then 3\|error code = 105\|PD unreachable' /tmp/init_$1.log || break; sleep 10; done; tail -2 /tmp/init_$1.log; fi; ./bin/start-hugegraph.sh 2>&1 | tail -1; for i in \$(seq 1 40); do curl -sf http://127.0.0.1:8080/graphs >/dev/null && break; sleep 3; done; echo \"8080: \$(curl -s --compressed http://127.0.0.1:8080/graphs) \$(curl -s --compressed http://127.0.0.1:8080/versions)\""
}


# cycle 4 (2026-09-21): master = 83ef9f3f (with #3220) so schema-dependent reads are meaningful.
# Variant A: batch entries left in the raft log at the swap (replay allocates at startup).
# Variant B: snapshot right before the stop (no replay), then the first client batch allocates.
# Both: rollback check (1.7.0 jars on the allocated mapping), remap workaround, full hg_data read, write, restart.
V=${1:-A}
OUT=$H/upg4$V; $SSH $N1 "rm -rf $OUT; mkdir -p $OUT"
STORES="127.0.0.1 192.168.80.236 192.168.80.237"
G='curl -s --compressed -m 20 -X POST http://127.0.0.1:8080/gremlin -H "Content-Type: application/json" -d'
count() { $SSH $N1 "$G '{\"gremlin\":\"$1\",\"aliases\":{\"graph\":\"DEFAULT-hugegraph\",\"g\":\"__g_DEFAULT-hugegraph\"}}' | python3 -c \"import sys,json; d=json.load(sys.stdin); print(d['result']['data'][0] if 'result' in d else 'ERR:'+d.get('message','')[:60])\" 2>/dev/null"; }
counts() { echo "$1: V=$(count 'g.V().count()') E=$(count 'g.E().count()') person=$(count 'g.V().hasLabel(\"person\").count()') cust=$(count 'g.V().hasLabel(\"cust\").count()') c200=$(count 'g.V(\"c200\").count()') p7knows=$(count 'g.V().has(\"person\",\"name\",\"p7\").outE(\"knows\").count()')"; }
gids() { $SSH $N1 "for h in $STORES; do for p in 1 4 8; do echo \"\$h p\$p \$(curl -s -m 5 http://\$h:8520/fix/graph_ids/\$p | cut -c1-200)\"; done; done"; }
allocs() { for N in $N1 $N2 $N3; do D=$STN; [ $N = $N1 ] && D=$ST1; $SSH $N "echo \"\$(hostname): \$(grep -c 'is allocated for graph DEFAULT/hugegraph' $D/logs/hugegraph-store.log) allocations of DEFAULT/hugegraph\""; done; }
snap() { $SSH $N1 "for h in $STORES; do curl -s -m 20 http://\$h:8520/test/snapshot >/dev/null; done"; sleep 25; for N in $N1 $N2 $N3; do D=$STN; [ $N = $N1 ] && D=$ST1; $SSH $N "echo \"\$(hostname): snapshot dirs=\$(ls -d $D/storage/raft/*/snapshot/snapshot_* 2>/dev/null | wc -l)\""; done; }
fullread() { $SSH $N1 "python3 ~/hg_data.py read http://127.0.0.1:8080 $OUT/$1.json >/dev/null 2>&1; python3 - <<'PY'
import json
a=json.load(open('$OUT/A-170-read.json')); b=json.load(open('$OUT/$1.json'))
ka={r['step']:json.dumps(r.get('data'),sort_keys=True) for r in a if r['step'].startswith('gremlin')}
kb={r['step']:json.dumps(r.get('data'),sort_keys=True) for r in b if r['step'].startswith('gremlin')}
same=[k for k in ka if ka[k]==kb.get(k)]; diff=[k for k in ka if ka[k]!=kb.get(k)]
print('$1 vs 1.7.0 baseline: identical %d/%d' % (len(same),len(ka)), ('differ: '+'; '.join(d[:60] for d in diff[:4])) if diff else '')
PY"; }
echo "== $(date +%T) variant $V: stop + restore 1.7.0 libs + wipe"
stop_all; use_lib 1.7.0
$SSH $N1 "rm -rf $PD/pd_data $ST1/storage; cd $SV && grep -q '^usePD' conf/rest-server.properties || printf 'usePD=true\npd.peers=192.168.80.235:8686\n' >> conf/rest-server.properties"
for N in $N2 $N3; do $SSH $N "rm -rf $STN/storage"; done
echo "== $(date +%T) start 1.7.0"
start_all rel170 init
echo "== $(date +%T) write A on 1.7.0 (batch)"
$SSH $N1 "python3 ~/hg_data.py write http://127.0.0.1:8080 $OUT/A-170-write.json 2>&1 | grep -v '^20[0-2]' | cut -c1-160"
counts "1.7.0 after write A"
echo "== $(date +%T) snapshot on every store"; snap
echo "== $(date +%T) write B on 1.7.0 (batch, 5 cust)"
$SSH $N1 "python3 - <<'PY'
import json,urllib.request
vs=[{\"label\":\"cust\",\"id\":\"c%d\" % i,\"properties\":{\"name\":\"post-snapshot-%d\" % i}} for i in range(200,205)]
req=urllib.request.Request('http://127.0.0.1:8080/graphs/hugegraph/graph/vertices/batch', data=json.dumps(vs).encode(), headers={'Content-Type':'application/json'}, method='POST')
print(urllib.request.urlopen(req).read()[:80])
PY"
counts "1.7.0 after write B"
$SSH $N1 "python3 ~/hg_data.py read http://127.0.0.1:8080 $OUT/A-170-read.json >/dev/null 2>&1; echo baseline-read-saved"
if [ "$V" = B ]; then echo "== $(date +%T) variant B: snapshot again so the raft log holds no batch entry at the swap"; snap; fi
echo "== $(date +%T) stop, swap to master libs (83ef9f3f), start, no client write"
stop_all; use_lib master
start_all master noinit
sleep 5
counts "master right after restart"
allocs
if [ "$V" = B ]; then
  fullread "B-master-before-write"
  echo "== $(date +%T) variant B: first client batch write on master (1 cust vertex)"
  $SSH $N1 "curl -s --compressed -m 20 -X POST http://127.0.0.1:8080/graphs/hugegraph/graph/vertices/batch -H 'Content-Type: application/json' -d '[{\"label\":\"cust\",\"id\":\"c400\",\"properties\":{\"name\":\"first-master-batch\"}}]' | cut -c1-80; echo"
  sleep 2
  counts "master after the first client batch"
  allocs
fi
gids
fullread "C-master-read"
$SSH $N1 "grep -a -c 'Failed to parse entry' $SV/logs/hugegraph-server.log | sed 's/^/Failed to parse entry lines in server log: /'"
echo "== $(date +%T) rollback check: 1.7.0 jars on the allocated mapping"
stop_all; use_lib 1.7.0
start_all rel170 noinit
sleep 20
counts "1.7.0 after rollback"
$SSH $N1 "cp $SV/logs/hugegraph-server.log $OUT/rollback-170-server.log; echo 'rollback server log, first errors:'; grep -a -n 'ERROR\|Exception\|cluster' $SV/logs/hugegraph-server.log | grep -v 'at org\.\|at java\.' | head -6 | cut -c1-220; curl -s --compressed -m 10 http://127.0.0.1:8080/graphs; echo"
echo "== $(date +%T) forward again to master"
stop_all; use_lib master
start_all master noinit
counts "master again"
echo "== $(date +%T) workaround: map DEFAULT/hugegraph/g -> 65534 on every partition of every store"
$SSH $N1 "for h in $STORES; do for p in \$(seq 0 11); do curl -s -m 5 -X POST http://\$h:8520/fix/update_graph_id/\$p -H 'Content-Type: application/json' -d '{\"DEFAULT/hugegraph/g\":65534}' >/dev/null; done; done; echo remapped"
sleep 2
counts "master after remap"
fullread "D-master-remapped-read"
echo "== $(date +%T) write under the sentinel mapping, then restart the stores"
$SSH $N1 "curl -s --compressed -m 20 -X POST http://127.0.0.1:8080/graphs/hugegraph/graph/vertices -H 'Content-Type: application/json' -d '{\"label\":\"cust\",\"id\":\"c300\",\"properties\":{\"name\":\"under-sentinel\"}}' | cut -c1-80; echo"
counts "master after write under sentinel"
for N in $N1 $N2 $N3; do D=$STN; [ $N = $N1 ] && D=$ST1; $SSH $N "$J17; (cd $D && ./bin/stop-hugegraph-store.sh) >/dev/null 2>&1; sleep 1; $(pk 8500); rm -f $D/bin/pid; cd $D && ./bin/start-hugegraph-store.sh >/dev/null 2>&1; for i in \$(seq 1 30); do ss -tln | grep -q ':8500 ' && break; sleep 2; done; echo \"store restarted \$(hostname)\""; done
sleep 20
counts "master after store restart"
allocs
fullread "E-master-remapped-restarted-read"
echo "== $(date +%T) done variant $V"
