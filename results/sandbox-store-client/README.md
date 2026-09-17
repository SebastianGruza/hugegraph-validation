# Gremlin sandbox vs the hstore client's lazy scan threads (found with #2994 on 2026-09-16)

`OrderedKvIterator.initialize()` submits to the store client's `ExecutorPool`, whose worker threads are created
lazily on first use. When the first ordered range scan of the process is issued by a Gremlin query,
`HugeSecurityManager.checkAccess(ThreadGroup)` on the gremlin-server worker denies the thread creation and the query
fails with `500 Not allowed to access thread group via Gremlin`; it keeps failing from Gremlin until some
non-Gremlin caller has created the pool. With PR #2994 the paged range-index shapes take the ordered scan, so its
six `G PAGE` positive controls fail on hstore (`reports/pr-2994/459a2b2f/`).

Fix: whitelist the store client's scan-executor classes in the two `checkAccess()` checks of
`HugeSecurityManager`, as raft and sofa-rpc already are (branch `fix/sandbox-store-client-threads`).

Measured 2026-09-17 on the lab (hstore server on PD + 3 stores, suite data loaded, `hg_j8.py` 207 shapes, servers
built from `1a15e762` + #2994 `459a2b2f` with and without the fix):

| server | G paging positive controls (6 shapes × count/pages) | `Not allowed to access thread group` in the server log |
|---|---|---|
| pr2994m | 6 MISMATCH + 6 TARGET-ERR | 16 |
| pr2994m + fix | 12 OK | 0 |

Files: `j8_before-pr2994m.txt`, `j8_after-pr2994ms.txt`, `*-server.log`, `run-summary.log`. `SecurityManagerTest`
still denies `new Thread()` from a Gremlin script (12/13 on the lab; `testFile` fails on plain master on a Polish
locale, unrelated).
