# Git-submodule and project-operation lock certification

This test-org lane certifies the complete remaining DEN-2038 surface in
`zed-pkg/zed-cli#243` at its current-main-integrated, nested-invocation-hardened,
thread-affine, shared-ownership, recovery-ordered, and cooperative-install-locked
exact candidate commit:

```text
2b5897f6a5b5cf33fee7a5934a60d7aa3e70e0a0
```

The candidate was first merged with current product `main` through integration
PR `zed-pkg/zed-cli#245`, preserving the independent external GitOps dispatcher
and its tests. When `main` later advanced with DEN-3018 publish-ignore
diagnostics, the branch was reconciled through a true two-parent merge. The
locking/recovery files and DEN-3018 files are disjoint, and both complete feature
sets remain present.

The product now routes all of these operations through the same canonical
checkout-local `.zed/operation.lock` boundary:

- ordinary install and frozen restore;
- add, remove, uninstall, and initialization through the existing facades;
- `zed overtake --git-submodules`;
- eager `.zpkg-staging` recovery;
- modular-takeover recovery before Git work; and
- `zed install --git-submodules`, including Git synchronization, recursive
  submodule checkout, dependency resolution, materialization, adapter wiring,
  and lock finalization under one descriptor lifetime.

## Reentrant ownership invariants

The reentrancy cache is thread-local. An owned operation guard must therefore be
acquired, used, and dropped on one thread. The final candidate stores the actual
kernel `LockGuard` inside an `Rc`-owned object and keeps only a weak reference in
the thread-local lookup table. This provides three properties:

1. `OperationGuard` is naturally neither `Send` nor `Sync`;
2. every same-thread nested acquisition shares the same descriptor ownership;
3. dropping outer and inner handles in any order cannot release the operating-
   system lock before the final live handle is dropped.

Two `compile_fail` rustdoc contracts prove the public type cannot satisfy either
`Send` or `Sync`. A focused unit regression drops the outer guard first,
confirms a queued same-process OS-lock probe remains contended, drops the final
inner guard, then confirms acquisition succeeds.

## Why separate black-box tests

Product unit tests prove nested facade reuse, thread affinity, shared descriptor
lifetime, and superproject path selection. They do not by themselves prove that
independent operating-system processes contend on the same checkout identity,
that path aliases cannot escape ownership, that recovery coordinates different
Zed homes, or that Git synchronization and installation share one uninterrupted
ownership window.

Four test-org scripts exercise those boundaries using the real release
executable and disposable local Git repositories. A temporary `git` shim pauses
at `git submodule sync` after the CLI must already own the checkout. After the
test releases it, the shim `exec`s the exact host Git binary with the original
arguments.

## Eighteen certified process checks

### Takeover normal completion

1. Takeover owns `.zed/operation.lock` before Git transport begins.
2. A frozen install through a symlink alias remains blocked while takeover owns
   the checkout.
3. After release, takeover publishes complete adopted state and the frozen
   waiter succeeds.

### Owner termination

4. An independent install remains blocked behind a paused takeover.
5. Terminating the takeover process group releases the descriptor lock without
   deleting the lockfile or running stale-lock reclamation.
6. Pre-mutation termination preserves exact manifest bytes and leaves no
   adoption or recovery state.

### Nested takeover invocation

7. Takeover launched from `packages/client/src` owns the superproject root lock.
8. It never creates a nested `packages/client/src/.zed/operation.lock` identity.
9. A root-level frozen install blocks and succeeds behind nested takeover.

### Cooperative `install --git-submodules`

10. Cooperative install owns the superproject operation lock before
    `git submodule sync` begins.
11. A different-home frozen install remains blocked across synchronization,
    recursive checkout, and the full install/finalizer boundary.
12. The frozen waiter succeeds only after the cooperative install publishes a
    complete lockfile and the pinned child checkout.

The frozen waiter starts before `.zpkg.lock` exists. Against the previous CLI
path it would race and fail because sync ran before the installer facade acquired
ownership.

### Transaction recovery versus another owner

13. Different-home eager recovery remains blocked behind active checkout
    ownership.
14. Pending destination and backup bytes stay exact while blocked.
15. After release, recovery restores exact bytes and removes staging.
16. The recovered process completes frozen installation against adopted state.

### Recovery ordering inside modular takeover

17. A pending transaction is recovered before takeover reaches
    `git submodule sync`.
18. Exact backup bytes are restored before `.gitmodules` verification,
    synchronization, or adoption begins.

## Matrix and promotion boundary

The workflow runs on Ubuntu 24.04 and macOS 15. It:

- pins the CLI, `zed-interfaces`, and `zed-lock` to full commit IDs;
- uses read-only repository permissions and commit-pinned Actions;
- disables persisted checkout credentials;
- prevents Python bytecode writes and compiles all four harnesses through a
  runner-temporary cache;
- runs source diff checks and Rust formatting;
- runs focused `project_lock` and `git_submodules` library tests;
- runs both thread-affinity compile-fail rustdoc contracts;
- runs strict Clippy on Linux;
- builds the exact release executable;
- executes six contention, termination, recovery, and ordering scenarios; and
- prints and rejects any dirty product or harness checkout without cleanup or
  reset.

Evidence contains binary identity, process IDs, measured blocking intervals, CLI
version, and all eighteen named checks. No account, public registry, Cloudflare
resource, credential, Docker daemon, or persistent namespace participates.

Merge this test-org PR only after both platform jobs pass on the exact head.
Then product PR `zed-pkg/zed-cli#243` can be promoted if its complete matrix and
review state remain clean on the same product commit.

Linear: DEN-2038.
