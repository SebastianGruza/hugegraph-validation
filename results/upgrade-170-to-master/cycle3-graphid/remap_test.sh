#!/bin/bash
# Lab experiment (Sebastian's go, 2026-09-21): map DEFAULT/hugegraph/g back to graph id 65534 (0xFFFE)
# on every partition of every store, measure, then restore the previous mapping.
G() { curl -s --compressed -m 20 -X POST http://127.0.0.1:8080/gremlin -H "Content-Type: application/json" \
  -d "{\"gremlin\":\"$1\",\"aliases\":{\"graph\":\"DEFAULT-hugegraph\",\"g\":\"__g_DEFAULT-hugegraph\"}}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['result']['data'][0])" 2>/dev/null; }
measure() { echo "$1: V=$(G 'g.V().count()') E=$(G 'g.E().count()') person=$(G 'g.V().hasLabel(\"person\").count()') software=$(G 'g.V().hasLabel(\"software\").count()') cust=$(G 'g.V().hasLabel(\"cust\").count()') c99=$(G 'g.V(\"c99\").count()') p7=$(G 'g.V().has(\"person\",\"name\",\"p7\").count()')"; }
STORES="127.0.0.1 192.168.80.236 192.168.80.237"
measure "before"
: > /tmp/gid_backup.txt
for h in $STORES; do for p in $(seq 0 11); do
  id=$(curl -s -m 5 http://$h:8520/fix/graph_ids/$p | python3 -c "import sys,json; d=json.load(sys.stdin); print([k for k,v in d.items() if v.get('graph')=='DEFAULT/hugegraph/g'][0])")
  echo "$h $p $id" >> /tmp/gid_backup.txt
done; done
echo "backup: $(awk '{print $3}' /tmp/gid_backup.txt | sort | uniq -c | tr '\n' ' ')"
for h in $STORES; do for p in $(seq 0 11); do
  curl -s -m 5 -X POST http://$h:8520/fix/update_graph_id/$p -H "Content-Type: application/json" -d '{"DEFAULT/hugegraph/g":65534}' >/dev/null
done; done
sleep 2
measure "after remap to 65534"
echo "graph_ids p1 on .235: $(curl -s -m 5 http://127.0.0.1:8520/fix/graph_ids/1 | cut -c1-220)"
# a write while mapped to the sentinel: does it stay under 65534?
curl -s --compressed -m 20 -X POST http://127.0.0.1:8080/graphs/hugegraph/graph/vertices -H "Content-Type: application/json" -d '{"label":"cust","id":"c100","properties":{"name":"after-remap"}}' | cut -c1-120; echo
measure "after one write under the sentinel mapping"
grep -h "allocated for graph DEFAULT/hugegraph" /home/seba/rel-1.7.0/apache-hugegraph-incubating-1.7.0/apache-hugegraph-store-incubating-1.7.0/logs/*.log | grep "$(date +%F)" | wc -l | sed 's/^/new allocations today: /'
# restore
while read h p id; do curl -s -m 5 -X POST http://$h:8520/fix/update_graph_id/$p -H "Content-Type: application/json" -d "{\"DEFAULT/hugegraph/g\":$id}" >/dev/null; done < /tmp/gid_backup.txt
sleep 2
measure "after restore"
