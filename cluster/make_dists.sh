#!/usr/bin/env bash
# make_dists.sh <worktree> <tag> — from a built worktree create dist-<tag>-hstore (:8080) and dist-<tag>-rocksdb (:8081)
# with conf copied from the combined dists (PRs touch no conf/ files).
set -euo pipefail
H=$HOME; WT=$1; TAG=$2
SRC=$WT/hugegraph-server
CMB=$H/hugegraph/hugegraph-server
DIST=$(ls -d $SRC/apache-hugegraph-server-*/ 2>/dev/null | head -1); DIST=${DIST%/}
if [ -z "$DIST" ]; then TGZ=$(ls $SRC/apache-hugegraph-*server*.tar.gz | head -1); tar -xzf "$TGZ" -C $SRC; DIST=$(ls -d $SRC/apache-hugegraph-server-*/ | head -1); DIST=${DIST%/}; fi
echo "$TAG dist: $DIST ($(ls $DIST/lib | wc -l) jars)"
for pair in "dist-$TAG-hstore:$CMB/apache-hugegraph-server-1.7.0" "dist-$TAG-rocksdb:$CMB/dist-rocksdb"; do
  name=${pair%%:*}; conf_src=${pair#*:}
  rm -rf "$SRC/$name"; cp -r "$DIST" "$SRC/$name"
  rm -rf "$SRC/$name/conf"; cp -r "$conf_src/conf" "$SRC/$name/conf"
  rm -rf "$SRC/$name/logs"/* "$SRC/$name/bin/pid" "$SRC/$name/rocksdb-data" 2>/dev/null || true
  echo "  $name: $(grep -E '^backend=' $SRC/$name/conf/graphs/hugegraph.properties) $(grep -E '^restserver.url' $SRC/$name/conf/rest-server.properties)"
done
ls -la $SRC/dist-$TAG-hstore/lib | grep -E "hugegraph-(core|hstore|api)-" | awk '{print $5, $9}'
