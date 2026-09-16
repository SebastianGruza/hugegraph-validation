#!/usr/bin/env bash
# k3s_coldstart_fresh.sh <chart_dir> [namespace=hg-cold] [wait_s=300]
# The #3203 shape on a fresh cluster: install the chart and scale the Stores to 0 at once (a cluster whose Stores are "slow"),
# watch the Server pods for wait_s seconds (the image gate HG_SERVER_STARTUP_TIMEOUT_S should keep them waiting
# instead of exiting), then scale the Stores to 3 and expect the Servers to turn Ready without a single restart.
set -u
CH=${1:?chart dir}; NS=${2:-hg-cold}; W=${3:-300}; F=hgcold-hugegraph   # chart fullname for release hgcold
echo "## $(date +%T) install $NS with store.replicas=0 (chart $(git -C "$CH" rev-parse --short HEAD 2>/dev/null))"
# values.schema.json refuses store.replicas=0, so install normally and scale the Stores away before any of them registers
helm upgrade --install hgcold "$CH/helm/hugegraph" -n "$NS" --create-namespace -f "$CH/helm/hugegraph/values-cluster.yaml" \
  --set hubble.enabled=false 2>&1 | tail -1
kubectl -n "$NS" scale sts $F-store --replicas=0
echo "$(date +%T) stores scaled to 0 right after install; store pods now: $(kubectl -n "$NS" get pods -l app.kubernetes.io/component=store --no-headers 2>/dev/null | wc -l)"
T0=$(date +%s)
for i in $(seq 1 $((W / 15))); do
  sleep 15
  echo "$(date +%T) t+$(( $(date +%s) - T0 ))s $(kubectl -n "$NS" get pods -l app.kubernetes.io/component=server -o jsonpath='{range .items[*]}{.metadata.name}:{.status.phase}/ready={.status.containerStatuses[0].ready}/restarts={.status.containerStatuses[0].restartCount}/state={.status.containerStatuses[0].state}{"  "}{end}' | sed 's/{"running":{"startedAt":"[^"]*"}}/running/g' | cut -c1-400)"
done
echo "## $(date +%T) server log tail (first server pod) before scaling stores:"
P=$(kubectl -n "$NS" get pods -l app.kubernetes.io/component=server -o jsonpath='{.items[0].metadata.name}')
kubectl -n "$NS" logs "$P" -c server --tail=12 2>/dev/null | grep -v "^[<>*{ ]" | cut -c1-200
echo "## $(date +%T) scaling stores to 3"
kubectl -n "$NS" scale sts $F-store --replicas=3
kubectl -n "$NS" rollout status sts $F-store --timeout=600s | tail -1
for i in $(seq 1 40); do
  R=$(kubectl -n "$NS" get pods -l app.kubernetes.io/component=server -o jsonpath='{range .items[*]}{.status.containerStatuses[0].ready}{" "}{end}')
  [ "$(echo $R | tr ' ' '\n' | grep -c true)" = "3" ] && { echo "$(date +%T) servers ready $((i * 5))s after stores"; break; }
  sleep 5
done
kubectl -n "$NS" get pods -o custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[0].ready,RESTARTS:.status.containerStatuses[0].restartCount,NODE:.spec.nodeName
PW=$(kubectl -n "$NS" get secret $F-admin -o jsonpath="{.data.password}" | base64 -d)
SVC=$(kubectl -n "$NS" get svc $F-server -o jsonpath="{.spec.clusterIP}")
curl -s -m 20 -u admin:$PW -o /dev/null -w "GET /graphs -> %{http_code}\n" "http://$SVC:8080/graphs"
curl -s -m 60 -u admin:$PW -H "Content-Type: application/json" -X POST "http://$SVC:8080/graphs/hugegraph/schema/propertykeys" \
  -d '{"name":"cold_pk","data_type":"INT","cardinality":"SINGLE"}' -o /dev/null -w "POST propertykey -> %{http_code}\n"
echo "## $(date +%T) done; uninstall with: helm uninstall hgcold -n $NS && kubectl delete ns $NS"
