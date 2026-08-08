# Git-submodule takeover operation-lock certification

This test-org lane certifies the complete remaining DEN-2038 surface in
`zed-pkg/zed-cli#243` at its current-main-integrated, nested-invocation-hardened,
thread-affine, shared-ownership, lint-clean, and recovery-ordered exact candidate
commit:

```text
1c74efd0bc137ef9905428a85aabe1fb1a0d2d05
```

The candidate was first merged with current product `main` through integration
PR `zed-pkg/zed-cli#245`, preserving the independent external GitOps dispatcher
and its tests. Later commits normalize formatting, keep macro-adjacent comments
lint-clean, move eager transaction recovery from the shared-home Store lock to
the canonical checkout operation lock, and make modular takeover recover pending
state before its first Git read or transport command.

After `main` advanced again with DEN-3018 publish-ignore diagnostics, the product
branch was reconciled through a true two-parent merge commit. The DEN-2038 lock
and recovery files and DEN-3018's `src/ops_entry.rs` plus
`docs/publish-ignore.md` are disjoint and both complete feature sets are present
in the final tree. No side was selected or discarded.

The product change routes `zed overtake --git-submodules` and eager
`.zpkg-staging` recovery through the same checkout-local `.zed/operation.lock`
boundary used by install, add, remove, and uninstall. It exposes an RAII guard
for multi-call library operations while preserving same-thread reentrancy. It
also resolves the owning `.gitmodules` superproject before acquiring the lock,
so an invocation from a nested source directory cannot create a second
operation-lock identity below the checkout root.

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
that symlink or nested-path aliases cannot escape ownership, that recovery uses
the same ownership boundary across different Zed homes, that modular takeover
recovers before Git synchronization, or that kernel ownership is released when
the owner is terminated.

The three test-org scripts exercise those boundaries using the real release
executable and disposable local Git repositories. A temporary `git` shim pauses
takeover after the CLI has acquired project ownership but before Git submodule
synchronization can proceed. The shim never replaces Git semantics; after
release it `exec`s the exact host Git binary with the original arguments.

## Fifteen certified process checks

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

This scenario would fail against the earlier implementation that locked the raw
current directory and only later discovered the superproject.

### Transaction recovery versus another owner

10. A process using a different Zed home cannot eagerly recover an active
    `.zpkg-staging` journal while takeover owns the checkout.
11. The pending destination and backup bytes remain exact throughout the
    observed blocking interval.
12. After ownership release, recovery restores the exact backup bytes and
    removes the staging journal.
13. The recovered process then completes frozen installation against the
    adopted manifest and lock state.

The recovery fixture is created only after takeover has reached the blocked Git
synchronization point. Against the old Store-home guard, the second process would
restore it concurrently; against the project lock, the journal remains untouched
until checkout ownership is released.

### Recovery ordering inside modular takeover

14. A pending transaction present before `zed overtake --git-submodules` is
    recovered before takeover reaches `git submodule sync`.
15. The exact backup bytes are restored before `.gitmodules` verification,
    transport synchronization, or adoption begins.

Modular takeover dispatch executes before `main`'s ordinary `run()` path. These
checks ensure recovery is explicitly performed at the start of the already-owned
takeover closure rather than deferred until the first `ProjectTransaction` after
Git work has already occurred.

## Matrix and promotion boundary

The workflow runs on Ubuntu 24.04 and macOS 15. It:

- pins the CLI, `zed-interfaces`, and `zed-lock` to full commit IDs;
- uses read-only repository permissions and commit-pinned Actions;
- disables persisted checkout credentials;
- prevents Python bytecode writes globally and compiles all three harnesses
  through a runner-temporary cache;
- runs source diff checks and Rust formatting;
- runs focused `project_lock` and `git_submodules` library tests;
- runs both thread-affinity `compile_fail` rustdoc contracts;
- runs strict Clippy on Linux;
- builds the exact release executable;
- executes five process-contention and ordering scenarios; and
- prints and rejects any dirty product or harness checkout without cleanup or
  reset.

Evidence contains the binary SHA-256, process IDs, observed blocking intervals,
CLI version, and all fifteen named process checks. No account, public registry,
Cloudflare resource, credential, Docker daemon, or persistent namespace
participates.

Merge this test-org PR only after both platform jobs pass on the exact head.
Then product PR `zed-pkg/zed-cli#243` can be promoted if its own complete matrix
and review state remain green on the same current-main-integrated product head.

Linear: DEN-2038.
