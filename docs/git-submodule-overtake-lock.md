# Git-submodule takeover operation-lock certification

This test-org lane certifies `zed-pkg/zed-cli#243` at its current-main-integrated,
nested-invocation-hardened, thread-affine, shared-ownership, and lint-clean exact
candidate commit:

```text
c1435af21d15a359853df3c51687df67d72af24a
```

The candidate was merged with current product `main` through integration PR
`zed-pkg/zed-cli#245` before the final locking review. That preserves the
independent external GitOps dispatcher and its tests; the reviewed file sets do
not overlap. The commits after the shared-ownership implementation are limited
to rustfmt layout and converting a cache description from rustdoc syntax to an
ordinary comment because rustdoc does not attach documentation to a
`thread_local!` macro invocation. No lock, API, or ownership semantics changed.

The product change routes `zed overtake --git-submodules` through the same
checkout-local `.zed/operation.lock` boundary used by install, add, remove, and
uninstall. It exposes an RAII guard for multi-call library operations while
preserving same-thread reentrancy. It also resolves the owning `.gitmodules`
superproject before acquiring the lock, so an invocation from a nested source
directory cannot create a second operation-lock identity below the checkout
root.

## Reentrant ownership invariants

The reentrancy cache is thread-local. An owned operation guard must therefore be
acquired, used, and dropped on one thread. The final candidate stores the actual
kernel `LockGuard` inside an `Rc`-owned object and keeps only a weak reference in
the thread-local lookup table. This provides three important properties:

1. `OperationGuard` is naturally neither `Send` nor `Sync`;
2. every same-thread nested acquisition shares the same descriptor ownership;
3. dropping outer and inner handles in any order cannot release the operating-
   system lock before the final live handle is dropped.

Two `compile_fail` rustdoc contracts prove the public type cannot satisfy either
`Send` or `Sync`. A focused unit regression acquires outer and inner guards,
drops the outer first, confirms a same-process OS-lock probe is still contended,
then drops the inner and confirms acquisition succeeds.

This replaces the earlier depth-only marker design, which could unlock too early
when independently returned nested RAII handles were dropped out of lexical
order.

## Why separate black-box tests

Product unit tests prove nested facade reuse, thread affinity, shared descriptor
lifetime, and superproject path selection. They do not by themselves prove that
independent operating system processes contend on the same checkout identity,
that symlink or nested-path aliases cannot escape ownership, or that kernel
ownership is released when the owner is terminated.

The two test-org scripts exercise those boundaries using the real release
executable and disposable local Git repositories. A temporary `git` shim pauses
takeover after the CLI has acquired project ownership but before Git submodule
synchronization can proceed. The shim never replaces Git semantics; after
release it `exec`s the exact host Git binary with the original arguments.

## Nine certified process checks

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

### Nested invocation

7. Takeover launched from `packages/client/src` owns the superproject root's
   `.zed/operation.lock` before Git synchronization.
8. The nested invocation never creates a second
   `packages/client/src/.zed/operation.lock` identity.
9. A root-level frozen install remains blocked and then succeeds behind that
   nested takeover.

This third scenario would fail against the earlier implementation that locked
the raw current directory and only later discovered the superproject.

## Matrix and promotion boundary

The workflow runs on Ubuntu 24.04 and macOS 15. It:

- pins the CLI, `zed-interfaces`, and `zed-lock` to full commit IDs;
- uses read-only repository permissions and commit-pinned Actions;
- disables persisted checkout credentials;
- compiles both Python harnesses outside the source tree;
- runs source diff checks and Rust formatting;
- runs focused `project_lock` and `git_submodules` library tests;
- runs both thread-affinity `compile_fail` rustdoc contracts;
- runs strict Clippy on Linux;
- builds the exact release executable;
- executes all three process-contention scenarios; and
- requires clean product and harness checkouts afterward.

Evidence contains the binary SHA-256, process IDs, observed blocking intervals,
CLI version, and all nine named process checks. No account, public registry,
Cloudflare resource, credential, Docker daemon, or persistent namespace
participates.

Merge this test-org PR only after both platform jobs pass on the exact head.
Then product PR `zed-pkg/zed-cli#243` can be promoted if its own complete matrix
and review state remain green on the same current-main-integrated product head.

Linear: DEN-2038.
