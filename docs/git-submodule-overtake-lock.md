# Git-submodule takeover operation-lock certification

This test-org lane certifies `zed-pkg/zed-cli#243` at its current-main-integrated
exact candidate commit:

```text
20e41810d94ef25d957fb8bda5d916e71211d2de
```

The candidate was merged with current product `main` through integration PR
`zed-pkg/zed-cli#245` before this final certification pin. That preserves the
independent external GitOps dispatcher and its tests; the reviewed file sets do
not overlap.

The product change routes `zed overtake --git-submodules` through the same
checkout-local `.zed/operation.lock` boundary used by install, add, remove, and
uninstall. It also exposes an RAII guard for multi-call library operations while
preserving same-thread reentrancy.

## Why a separate black-box test

The product unit test proves that an explicit guard can call a nested facade
without deadlocking. It does not by itself prove that two independent operating
system processes contend on the same checkout identity, that a symlink alias
converges on that identity, or that kernel ownership is released when the owner
is terminated.

`scripts/git_submodule_overtake_lock.py` tests those boundaries using the real
release executable and disposable local Git repositories. A temporary `git`
shim pauses takeover after the CLI has acquired project ownership but before
Git submodule synchronization can proceed. The shim never replaces Git
semantics; after release it `exec`s the exact host Git binary with the original
arguments.

## Six certified checks

### Normal completion

1. Takeover publishes and owns `.zed/operation.lock` before beginning Git
   transport work.
2. A frozen install started through a symlink alias remains blocked while
   takeover owns the checkout.
3. Releasing takeover allows it to publish the adopted manifest/lock first, and
   the waiting frozen install then succeeds against that complete state.

The waiter is intentionally launched before `.zpkg.lock` exists. Without the
shared operation boundary it would race ahead and fail; success after a measured
blocking interval demonstrates serialization rather than coincidental ordering.

### Owner termination

4. An independent ordinary install remains blocked behind a second paused
   takeover.
5. Terminating the complete takeover process group releases the descriptor lock,
   allowing the waiting install to proceed without polling or manual lockfile
   deletion.
6. Because termination occurs before Git synchronization or Zed mutation, the
   authored manifest remains byte-identical, no dependency is adopted, and no
   `.zpkg-staging` recovery state is published.

## Matrix and promotion boundary

The workflow runs on Ubuntu 24.04 and macOS 15. It:

- pins the CLI, `zed-interfaces`, and `zed-lock` to full commit IDs;
- uses read-only repository permissions and commit-pinned Actions;
- disables persisted checkout credentials;
- compiles the Python harness outside the source tree;
- runs source diff checks and Rust formatting;
- runs focused `project_lock` and `git_submodules` library tests;
- runs strict Clippy on Linux;
- builds the exact release executable;
- executes both process-contention scenarios; and
- requires clean product and harness checkouts afterward.

Evidence contains the binary SHA-256, process IDs, observed blocking intervals,
CLI version, and the six named checks. No account, public registry, Cloudflare
resource, credential, Docker daemon, or persistent namespace participates.

Merge this test-org PR only after both platform jobs pass on the exact head.
Then product PR `zed-pkg/zed-cli#243` can be promoted if its own complete matrix
and review state remain green on the same current-main-integrated product head.

Linear: DEN-2038.
