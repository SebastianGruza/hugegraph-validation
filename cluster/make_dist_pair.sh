#!/usr/bin/env bash
# make_dist_pair.sh <build_root> <name> — on the server node: from a server build under <build_root>/hugegraph-server
# create two hstore server distributions sharing the PD cluster:
#   <build_root>/hugegraph-server/<name>     server A  REST 8080 / gremlin 8182   (conf copied from the combined hstore dist)
#   <build_root>/hugegraph-server/<name>-B   server B  REST 8082 / gremlin 8184
set -euo pipefail
ROOT=${1:?build root, e.g. ~/hg-master}; NAME=${2:?dist name}
SRC=$ROOT/hugegraph-server
CONF=$HOME/hugegraph/hugegraph-server/apache-hugegraph-server-1.7.0/conf
DIST=$(ls -d $SRC/apache-hugegraph-server-* | head -1)
for suffix in "" "-B"; do
  D=$SRC/$NAME$suffix
  rm -rf "$D"; cp -r "$DIST" "$D"; rm -rf "$D/conf"; cp -r "$CONF" "$D/conf"
  rm -rf "$D"/logs/* "$D/bin/pid" 2>/dev/null || true
  # gremlin-server.yaml ships with the port commented out; bind it explicitly so A and B never collide
  sed -i -E 's|^#?port: .*|port: 8182|' "$D/conf/gremlin-server.yaml"
  # the shipped rest-server.properties has no gremlinserver.url; start-hugegraph.sh then assumes 8182
  grep -q '^gremlinserver.url=' "$D/conf/rest-server.properties" || echo 'gremlinserver.url=127.0.0.1:8182' >> "$D/conf/rest-server.properties"
  if [ "$suffix" = "-B" ]; then
    sed -i 's|^restserver.url=.*|restserver.url=http://127.0.0.1:8082|; s|^gremlinserver.url=.*|gremlinserver.url=127.0.0.1:8184|' "$D/conf/rest-server.properties"
    sed -i -E 's|^#?port: .*|port: 8184|' "$D/conf/gremlin-server.yaml"
    grep -q "^arthas" "$D/conf/rest-server.properties" && sed -i 's|^arthas.telnet_port=.*|arthas.telnet_port=8572|; s|^arthas.http_port=.*|arthas.http_port=8571|' "$D/conf/rest-server.properties"
  fi
  echo "$D: $(grep -E '^restserver.url|^gremlinserver.url' $D/conf/rest-server.properties | tr '\n' ' ') gremlin-yaml=$(grep -E '^port:' $D/conf/gremlin-server.yaml | tr -d ' ') core=$(ls -la $D/lib/hugegraph-core-*.jar | awk '{print $5}')"
done
