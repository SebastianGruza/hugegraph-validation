# Contract fixtures as a regression detector: apache/hugegraph#3184 (2026-09-20)

The client contract scenario from apache/hugegraph-toolchain#772 (PR 1, 17 fixtures) recorded three times with the
same Java client (toolchain master) against three servers on the lab:

| set | server | backend | sort-key prefix (`asset=ETC`) | sort-key range (`asset=ETC, epoch>=150`) | Gremlin `outE().has('asset','ETC')` |
|---|---|---|---|---|---|
| `rel170-rocksdb` | release 1.7.0, pristine tarball, CI auth config | rocksdb | 200, 2 edges | 200, 1 edge | 200, `[100, 200]` |
| `rel170-hstore` | release 1.7.0, pristine tarball | hstore (lab PD + 3 stores), fresh graph `contract17b` | **500** `Can't construct Cardinality from code 0` | **500** `Unsupported data type UNKNOWN` | **500** same store scan error |
| `master-1a15e762-hstore` | master at the #3184 merge commit | hstore, fresh graph `contractm` | 200, 2 edges | 200, 1 edge | 200, `[100, 200]` |

The scenario reproduces the #3184 shape on purpose: property keys created in the order `amount` (DOUBLE), `asset`,
`epoch`; edge label `transfer` with `sort_keys [asset, epoch]` and `frequency MULTIPLE`; three edges from one vertex.
Everything else in the three sets is identical up to the placeholders (`{graph}`, masked ids/timestamps) and the api
version field (`0.71` vs `0.72`); see the two `diff-*.txt` files.

What this shows for #772 / #3110: a language-neutral fixture set recorded from the reference client is enough to
tell a backend-specific server regression apart from a client change, without any backend-specific test code. The
three error bodies are the wire form a client sees for that bug (HTTP 500, `exception`, `message`, `cause`, `trace`).

Servers: `~/rel-1.7.0/server-contract` (.235:8083, rocksdb, auth on), `~/rel-1.7.0/server-contract17-hstore`
(.235:8084), `~/hg-master1a15/hugegraph-server/dist-contractm-hstore` (.235:8085). Recorded via ssh tunnels with
`-Dcontract.record=true -Dcontract.url=... -Dcontract.graph=... -Dcontract.fixtures=<dir>`.
