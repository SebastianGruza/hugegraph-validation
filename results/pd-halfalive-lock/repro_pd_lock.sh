#!/usr/bin/env bash
# Reproduce a PD that "starts" without its RocksDB: hold an fcntl lock on pd_data/rocksdb/LOCK of a COPY of a PD
# data directory while PD starts on that copy (ports shifted +1 so the live PD is untouched), release after 40 s.
# Expected (2026-09-21, master 83ef9f3f): "Failed to open RocksDB ... LOCK: Resource temporarily unavailable" once,
# "Started HugePDServer", then forever: /v1/health 200, /v1/ready 503 STATE_UNINITIALIZED, REST 401, no retry, no exit.
set -u
PD=${PD:-$HOME/rel-1.7.0/apache-hugegraph-incubating-1.7.0/apache-hugegraph-pd-incubating-1.7.0}
D=$HOME/pd-dup; rm -rf $D; mkdir -p $D/logs; cp -r $PD/bin $PD/conf $PD/lib $D/; cp -r $PD/pd_data $D/pd_data; cd $D
sed -i "s/^  port: 8686/  port: 8687/; s/^  port: 8620/  port: 8621/; s#address: \(.*\):8610#address: \1:8611#; s#peers-list: \(.*\):8610#peers-list: \1:8611#" conf/application.yml
rm -f bin/pid
nohup python3 -c "
import fcntl,time
f=open('$D/pd_data/rocksdb/LOCK','r+'); fcntl.lockf(f, fcntl.LOCK_EX); time.sleep(40)
" >/dev/null 2>&1 &
sleep 1; ./bin/start-hugegraph-pd.sh | tail -1
sleep 70
for p in health ready members; do printf "%-8s " $p; curl -s -m 5 -w " HTTP %{http_code}" http://127.0.0.1:8621/v1/$p | cut -c1-120; echo; done
grep -a -c "Failed to open RocksDB" logs/hugegraph-pd-stdout.log | sed 's/^/open attempts logged: /'
