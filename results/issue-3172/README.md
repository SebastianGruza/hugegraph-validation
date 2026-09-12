# apache/hugegraph#3172 — PD member RPC NPE on 1.7.0, reproduced on 1 and 3 PD nodes (2026-09-12)

Lab of [docs/setup.md](../../docs/setup.md), three VMs. Two PD builds side by side: the official
`apache-hugegraph-incubating-1.7.0` PD (JDK 11) and the lab's master build `60c8803` (JDK 17).
Driver: [`cluster/pd3.sh`](../../cluster/pd3.sh) (`<rel|lab> cycle` = configure the 3 nodes as one raft group, fresh
start, check every node, stop all, restart with the previous leader last, check again).

## Root cause (static, confirmed by the stack trace below)

1.7.0 `RaftRpcClient.init()` calls `rpcClient.init(null)`, so jraft's `BoltRpcClient.opts` is null; and
`internalCallAsyncWithRpc()` passes `invokeCtx = null`. In jraft 1.3.13 `BoltRpcClient.getBoltInvokeCtx(null)`
evaluates `this.opts.isEnableRpcChecksum()` → `NullPointerException` with no message, thrown synchronously inside
`invokeAsync`, caught by `catch (Throwable t)` which logs only `t.getMessage()` → `failed to call rpc to X. null`.
Every `GetMemberRequest` RPC therefore fails, for every peer including the node itself, before anything is sent.
Master avoids the branch since `b9a3dd9` (`invokeCtx = new InvokeContext()`), but still calls `rpcClient.init(null)`.

## 3-node cycle

| | official 1.7.0 (`pd3-1.7.0-cycle.log`) | master `60c8803` (`pd3-master-cycle.log`) |
|---|---|---|
| fresh start, `/v1/members` on the leader | `pdLeader: null`, 3 × `Offline`, `grpcUrl: ""` | leader `192.168.80.235:8610`, 3 × `Up`, every `grpcUrl` filled |
| fresh start, `/v1/members` on a follower | **HTTP 500**, `ExecutionException: NullPointerException` from `RaftEngine.getLeaderGrpcAddress(RaftEngine.java:242)` (`follower-members-npe-1.7.0.txt`) | same answer as on the leader (redirected) |
| stop all, restart with the old leader last | leader moved; the behaviour is unchanged: followers 500, leader shows 3 × `Offline` | leader moved 235 → 236; all three nodes answer, 3 × `Up`, one transient `failed to call rpc` on the node that started first |
| stores pointing at a follower | cannot register (`Exception in storage registration ... pd=192.168.80.235:8686`) | n/a |

So on 1.7.0 a follower is broken from the first start, not only after a leader change: any request that has to be
redirected (`/v1/members`, store registration, PD client calls landing on a follower) dies in the same NPE.

## Single node

`single-node-1.7.0-rpc-failures.txt`: ~9 400 `failed to call rpc to 127.0.0.1:8610. null` lines within ~10 s of
starting a single 1.7.0 PD (the self-query), none on master.
