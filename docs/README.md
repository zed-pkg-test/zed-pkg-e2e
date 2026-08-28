# zed-pkg E2E documentation

This directory records the cross-repository acceptance contracts maintained by
`zed-pkg-test/zed-pkg-e2e`. Each canary is intended to prove behavior through a
real compiled `zed` binary and disposable Git/package fixtures rather than only
through unit-level implementation tests.

## Git submodule certification

The Git-submodule surface is split into complementary contracts so failures are
attributable and the security boundaries remain explicit:

- [`global-git-submodule-mode-canary.md`](global-git-submodule-mode-canary.md)
  certifies the shared `--git-submodules[=true|false]` and
  `ZED_PKG_GIT_SUBMODULES` contract for both `zed install` and
  `zed overtake`, including recursive initialization, explicit CLI precedence,
  migration into `.zpkg.toml` and `.zpkg.lock`, and idempotent takeover.
- [`git-submodule-interop-canary.md`](git-submodule-interop-canary.md) certifies
  baseline interoperability between Zed packages and Git-managed submodules,
  including repositories where Git and Zed retain authority over different
  paths.
- [`git-submodule-pack-canary.md`](git-submodule-pack-canary.md) certifies the
  archive, pack, and publication boundary so submodule metadata or unchecked
  worktree state cannot silently enter a package artifact.

Together these contracts cover transport, ownership migration, lock provenance,
replay, and publication safety without treating Git submodules and Zed packages
as mutually exclusive project models.

## Canary requirements

A permanent cross-repository canary should:

1. pin every product, interface, fixture, and third-party Action input to an
   immutable commit SHA;
2. use read-only GitHub permissions unless a narrower write is explicitly part
   of the behavior under test;
3. disable persisted checkout credentials and clear inherited product tokens
   before invoking `zed`;
4. create disposable repositories, registries, homes, consumers, and worktrees
   per job;
5. exercise the release binary through its public CLI rather than importing
   private implementation modules;
6. verify the source checkout remains clean after the run; and
7. retain bounded diagnostics or evidence sufficient to reproduce a failed
   invariant.

## Change process

Behavioral changes should update the implementation repository and the matching
acceptance contract in separate pull requests. During review, the canary pins
the exact implementation PR head. After merge, that pin moves to the shipped
merge commit so `main` continues to certify the artifact users actually receive.
