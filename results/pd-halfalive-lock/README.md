# PD keeps running after failing to open its RocksDB (lock held by the previous instance), 2026-09-21

Lab reproduction of the POC incident (three PDs restarted by systemd during a load; the new JVMs started before the
old ones released `pd_data/rocksdb/LOCK`; all three came up with ports open and answered 401 to everything; stores
lost the PD leader; the cluster stood still until the LOCK files were removed by hand).

`repro_pd_lock.sh`: start PD on a copy of a PD data dir while another process holds an fcntl lock on
`pd_data/rocksdb/LOCK` for 40 s. Result (`pd-halfalive-stdout.log`, config `pd-halfalive-application.yml`):

```
15:19:10 ERROR HgKVStoreImpl - Failed to open RocksDB from ./pd_data/rocksdb/
         RocksDBException: While lock file: ./pd_data/rocksdb//LOCK: Resource temporarily unavailable
15:19:10 ERROR HgKVStoreImpl - Failed to open data file,{}
15:19:13 INFO  HugePDServer - Started HugePDServer in 5.856 seconds
```

Three minutes after the lock was released: `/v1/health` 200, `/v1/ready` 503 `STATE_UNINITIALIZED`, `/v1/members`
401, gRPC and raft ports listening, "Failed to open RocksDB" logged exactly once (no retry), process alive.
Cause: `HgKVStoreImpl.init()` catches the `PDException` from `openRocksDB()` and only logs it; `db` stays null and
Spring Boot completes startup. A variant with the lock held on the live PD's own directory (raft log locked as well)
ended with the process exiting after about two minutes, so the half-alive state needs the raft side to open.
