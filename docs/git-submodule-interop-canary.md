# Git-submodule interoperability canary

This repository independently certifies the consolidated Git-submodule surface
in `zed-pkg/zed-cli#108` rather than relying only on product-repository tests.
The workflow pins these immutable inputs:

```text
zed-pkg/zed-cli@b2ec50cd1d7c182bd9219795cd2918387c9c4cd8
zed-pkg/zed-interfaces@c2e049006453c26ca8ca291783f681fce75cb01f
```

The same CLI commit is recorded in `.zed-cli-ref`, so this PR also exercises the
candidate through the repository's full lifecycle suite across all maintained
fixture repositories.

## Contract

`scripts/git_submodule_interop.py` creates disposable local Git repositories and
certifies twelve black-box behaviors through the compiled public `zed` binary:

1. `--git-submodules=false` overrides an inherited truthy environment setting.
2. Cooperative install initializes top-level and nested submodules recursively.
3. Takeover records exact workspace, direct dependency, commit, digest, and size
   provenance.
4. A fresh clone replays frozen state without changing one lockfile byte.
5. Dirty adopted content fails before lock mutation.
6. Committed branch-provenance drift fails before lock mutation.
7. A mixed repository adopts only the explicit Zed package and leaves the
   ordinary submodule Git-managed.
8. Mixed fresh-clone frozen replay initializes both Git transports, preserves
   manifest and lock bytes, and materializes only the adopted package through
   `zed_modules`.
9. An all-Git-only takeover may initialize requested transport but publishes no
   Zed lock, modules, or recovery state and preserves the authored manifest.
10. Malformed package intent fails closed before any Zed mutation.
11. A pre-commit resolution failure restores exact authored manifest bytes.
12. The same failure removes a takeover-generated manifest from a previously
    manifestless root.

The product's four dedicated integration binaries add direct coverage for CLI
and environment precedence, nested synchronization, idempotent takeover,
manifestless rollback, true clone-without-submodules restoration, clean recursive
status, and branch metadata refresh without replacing the immutable gitlink pin.
Unit and Clippy coverage also enforce that only true `NotFound` is skippable:
directory-valued, dangling, symlinked, and invalid `.zpkg.toml` entries fail
closed.

## Evidence and safety

Each Ubuntu 24.04 and macOS 15 job retains:

- the complete command transcript;
- adopted and mixed-repository lock evidence;
- the release binary SHA-256;
- the exact CLI version; and
- the twelve certified contract checks.

The workflow has only `contents: read`, disables persisted checkout credentials,
pins third-party Actions to full commit SHAs, verifies the CLI/interface/source
pins, keeps Python bytecode outside checked-out sources, denies Clippy warnings,
and requires clean product, interface, and harness checkouts afterward.

No account, public package registry, credential, Docker daemon, or persistent
namespace participates. The canary explicitly removes inherited Zed and GitHub
tokens from product subprocesses. Local file transport is enabled only through
the acceptance process's explicit Git configuration.

A manually dispatched optional job repeats the same black-box contract on the
registered `[self-hosted, linux, sonus-ci]` ARC scale set when hosted runner
capacity is constrained.

## Local execution

From sibling checkouts:

```bash
cargo build --locked --release \
  --manifest-path ../zed-cli/Cargo.toml \
  --bin zed

python3 scripts/git_submodule_interop.py \
  --zed ../zed-cli/target/release/zed \
  --work-root /tmp/zed-git-submodule-interop
```

The work root must not already exist. It is retained on failure for inspection.
