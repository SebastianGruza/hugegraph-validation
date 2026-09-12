#!/usr/bin/env bash
# run_chain.sh — 20M index re-measure, then the four-sort-key experiment at replication 1 and 3 (PD default-shard-count).
S=$HOME/hugegraph-validation/cluster
PDY=/home/seba/hugegraph/hugegraph-pd/apache-hugegraph-pd-1.7.0/conf/application.yml; N1=seba@192.168.80.235
set_shards() { ssh -n -o BatchMode=yes $N1 "sed -i 's/^\(\s*default-shard-count:\s*\).*/\1$1/' $PDY; grep -n 'default-shard-count' $PDY"; }
bash $S/idx_run_variant.sh 20m-v2 index 20000000
set_shards 1
for V in sk4-noindex sk4-index; do bash $S/idx_run_variant.sh sk4-r1 $V 5000000; done
set_shards 3
for V in sk4-noindex sk4-index; do bash $S/idx_run_variant.sh sk4-r3 $V 5000000; done
set_shards 1
echo "== CHAIN DONE $(date +%T)"
