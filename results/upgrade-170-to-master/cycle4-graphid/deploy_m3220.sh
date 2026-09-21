#!/usr/bin/env bash
# Unpack the freshly built master (83ef9f3f) dists into ~/mst on .235 and copy the store lib to the two store nodes.
set -uo pipefail
N1=seba@192.168.80.235; N2=seba@192.168.80.236; N3=seba@192.168.80.237
SSH="ssh -n -o ConnectTimeout=15 -o BatchMode=yes"
H=/home/seba; M=$H/mst; W=$H/hg-m3220
$SSH $N1 "rm -rf $M && mkdir -p $M && cd $M && tar xzf $W/hugegraph-pd/apache-hugegraph-pd-1.7.0.tar.gz && tar xzf $W/hugegraph-store/apache-hugegraph-store-1.7.0.tar.gz && tar xzf $W/hugegraph-server/apache-hugegraph-server-1.7.0.tar.gz && ls $M && ls -la --time-style=+%F_%R $M/apache-hugegraph-server-1.7.0/lib/hugegraph-core-*.jar $M/apache-hugegraph-store-1.7.0/lib/hg-store-core-*.jar | awk '{print \$6, \$7}'"
for N in $N2 $N3; do ssh -o ConnectTimeout=15 -o BatchMode=yes $N1 "cd $M/apache-hugegraph-store-1.7.0 && tar cf - lib" | $SSH $N "rm -rf $H/mst-store-lib && mkdir -p $H/mst-store-lib && cd $H/mst-store-lib && tar xf - && echo \"\$(hostname): \$(ls lib | wc -l) jars\""; done
