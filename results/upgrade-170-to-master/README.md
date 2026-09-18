# Upgrade 1.7.0 release -> master jars (2026-09-18)

Lab: PD + 3 stores (192.168.80.235/.236/.237), server on .235. Official
`apache-hugegraph-incubating-1.7.0` binaries, master = `4f5202b8`. Upgrade
method: stop everything without wiping, replace `lib/` with the master jars
in PD, all stores and the server (conf and data dirs untouched), start.

| cycle | config | rocksdb | hstore |
|---|---|---|---|
| 1 | `usePD` unset (default false) | all schema + data visible, 39 V / 63 E identical | all schema + data visible, identical |
| 2 | `usePD=true`, `pd.peers` set | n/a | graph listed, `g.V().count()` works, **schema empty**, every label/property "Undefined" |

Cause (cycle 2): `HUGEGRAPH/<cluster>/...` prefix of the PD meta keys.
1.7.0 wrote the schema under `hg-test` (`ServerOptions.CLUSTER` default,
`GraphManager` connected first). master connects first from
`StandardHugeGraph` with the hardcoded `hg`, because #3008 moved
`HugeGremlinServer.prepare()` (which opens every graph from `conf/graphs`)
before `HugeRestServer.start()`. `MetaManager.connect()` is a no-op after
the first call, so the configured cluster name is ignored.
`pd-keys-after-cycle2.txt`: 106 keys under `hg-test/.../hugegraph`
(1.7.0), 36 under `hg/.../hugegraph` (created fresh by master).

Scripts: `hg_data.py` (write/read), `phaseA_release.sh`, `phaseC_swap.sh`,
`cycle2_usepd.sh`, `PdKeys.java` (prefix listing through `KvClient`).
Logs: `A-*` 1.7.0 writes (the `hstore`/`rocksdb` pair is the first, broken
attempt; `8080`/`8081` the good one), `D-master-*` reads after the swap
(`8080-read` was taken while node2/node3 still ran 1.7.0 store jars,
`8080-read2` after all stores were swapped), `C2-*` the usePD=true cycle,
`C2-master-nolocal-read` the attempted workaround (graph config removed from
`conf/graphs`: no graph at all, the graph was never registered in PD meta).
