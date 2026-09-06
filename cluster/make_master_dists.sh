#!/usr/bin/env bash
# make_master_dists.sh — on the server node: from the master build in ~/hg-master create
#   ~/hg-master/hugegraph-server/dist-master-hstore   (conf copied from the combined hstore dist,  port 8080)
#   ~/hg-master/hugegraph-server/dist-master-rocksdb  (conf copied from the combined rocksdb dist, port 8081)
# The PRs under test touch no conf/ files, so the configurations carry over unchanged.
set -euo pipefail
H=$HOME
SRC=$H/hg-master/hugegraph-server
CMB=$H/hugegraph/hugegraph-server
DIST=$(ls -d $SRC/apache-hugegraph-server-* 2>/dev/null | head -1 || true)
if [ -z "$DIST" ]; then
  TGZ=$(ls $SRC/hugegraph-dist/target/apache-hugegraph-*server*.tar.gz 2>/dev/null | head -1 || true)
  [ -n "$TGZ" ] || { echo "no dist dir and no tarball under $SRC"; exit 1; }
  tar -xzf "$TGZ" -C $SRC && DIST=$(ls -d $SRC/apache-hugegraph-server-* | head -1)
fi
echo "master dist: $DIST ($(ls $DIST/lib | wc -l) jars)"
for pair in "dist-master-hstore:$CMB/apache-hugegraph-server-1.7.0" "dist-master-rocksdb:$CMB/dist-rocksdb"; do
  name=${pair%%:*}; conf_src=${pair#*:}
  rm -rf "$SRC/$name"; cp -r "$DIST" "$SRC/$name"
  rm -rf "$SRC/$name/conf"; cp -r "$conf_src/conf" "$SRC/$name/conf"
  rm -rf "$SRC/$name/logs"/* "$SRC/$name/bin/pid" "$SRC/$name/rocksdb-data" 2>/dev/null || true
  echo "  $name: backend=$(grep -E '^backend=' $SRC/$name/conf/graphs/hugegraph.properties) rest=$(grep -E '^restserver.url' $SRC/$name/conf/rest-server.properties)"
done
echo "--- core/hstore jars in master dists (must be master, no .orig):"
ls -la $SRC/dist-master-hstore/lib | grep -E "hugegraph-(core|hstore|api)-" | awk '{print $5, $9}'
echo "--- sanity: master vs combined core jar size"; ls -la $CMB/apache-hugegraph-server-1.7.0/lib/hugegraph-core-1.7.0.jar* | awk '{print $5, $9}'
