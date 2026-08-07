# Git-submodule interoperability canary

This repository independently certifies the Git-submodule implementation merged
by `zed-pkg/zed-cli#108` rather than relying only on product-repository tests.
The workflow pins these immutable shipped inputs:

```text
zed-pkg/zed-cli@b427e11fa9f592de88907320eff3293d0837b2b3
zed-pkg/zed-interfaces@c2e049006453c26ca8ca291783f681fce75cb01f
```

The same merged CLI commit is recorded in `.zed-cli-ref`, so this PR also
exercises the shipped implementation through the repository's full lifecycle
suite across all maintained fixture repositories. Before promotion, the
certification branch is merged with current E2E `main` and revalidated at the
resulting head; independent acceptance additions are preserved rather than
resolved by selecting one side of a divergent history.

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
This canary independently certifies the consolidated Git/Zed submodule behavior
in `zed-pkg/zed-cli#108`. It runs outside the product repository against one
exact immutable CLI commit:

```text
zed-pkg/zed-cli@b2ec50cd1d7c182bd9219795cd2918387c9c4cd8
```

The product feature builds on the merged cooperative-install and takeover work
in `zed-pkg/zed-cli#96` plus the failure-atomic migration hardening in #101.

## Contract under test

A superproject may use Git submodules for different purposes at the same time:

- a submodule containing a valid regular-file `.zpkg.toml` can be adopted into
  the root Zed workspace, direct dependency intent, deterministic
  materialization, and additive `[[git-submodule]]` lock provenance;
- a submodule without `.zpkg.toml` remains Git-managed and never appears in the
  Zed manifest or lock; and
- ordinary `zed install --git-submodules` still initializes every configured
  Git submodule, whether Zed adopted it or not.

Missing package intent is the only skip condition. A present but invalid,
directory-valued, dangling, or symlinked package manifest remains an error.

## Product certification included at the pin

The consolidated product candidate also includes focused real-process suites for:

- opt-in defaults and CLI/environment boolean precedence;
- top-level and nested recursive initialization;
- takeover idempotence and preservation of `.gitmodules` and gitlinks;
- rollback for failed takeover, including a manifestless root;
- fresh-clone frozen restoration with clean recursive Git status; and
- configured branch provenance that never replaces the immutable gitlink pin.

The workflow runs all four dedicated integration binaries before the independent
Python canary.

## Black-box scenarios

`scripts/git_submodule_interop.py` creates real local Git repositories and runs
the compiled public `zed` executable. It does not call Rust library internals.

### Mixed takeover and frozen replay

The harness creates one Zed package submodule and one ordinary Git-only
submodule, then verifies:

1. `zed overtake --git-submodules` adopts exactly one package;
2. the Git-only path is reported and absent from `.zpkg.toml` and `.zpkg.lock`;
3. the adopted package has exact workspace/dependency intent and immutable Git
   lock provenance;
4. the resulting Zed manifest and lock can be committed normally;
5. a fresh `git clone --no-recurse-submodules` starts with neither submodule
   initialized;
6. `ZED_PKG_GIT_SUBMODULES=yes zed install --frozen` initializes both
   submodules;
7. frozen replay preserves committed manifest and lock bytes exactly; and
8. the adopted package is materialized through `zed_modules`, while the ordinary
   submodule remains solely Git-managed.

### Git-only migration boundary

A repository whose only submodule has no `.zpkg.toml` is synchronized, but
`overtake` returns an actionable failure and does not create or change:

- `.zpkg.toml`;
- `.zpkg.lock`;
- `zed_modules/`; or
- `.zpkg-staging/`.

This distinguishes intentional Git transport side effects from Zed authority
migration.

### Invalid package boundary

A submodule containing malformed `.zpkg.toml` is not silently treated as
Git-only. Takeover fails before changing the root manifest, producing a lock, or
materializing a package. Product-focused tests at the same pinned commit also
prove that directory and symlink manifest entries fail the regular-file gate.

## Workflow gates

`.github/workflows/git-submodule-interop.yml`:

- requires a full 40-character CLI pin;
- uses read-only repository permissions;
- pins checkout and artifact Actions to immutable commits;
- disables persisted checkout credentials;
- keeps Python bytecode outside checked-out sources;
- compiles the Python canary;
- checks both repositories with `git diff --check`;
- runs product `rustfmt`, all four dedicated Git-submodule integration suites,
  and all-target Clippy with warnings denied;
- builds the exact release executable;
- runs the independent black-box harness from a fresh work root;
- requires clean harness and product source trees afterward; and
- uploads bounded result or failure evidence for 14 days.

The normal gate uses Ubuntu 24.04. A manually dispatched optional job can run the
same canary on the registered `[self-hosted, linux, sonus-ci]` ARC scale set when
hosted Actions capacity is constrained.

No registry account, package namespace, GitHub token, cloud credential, Docker
daemon, or persistent runner state participates. All package and submodule
transports are disposable local filesystem repositories.

## Local execution

Build the pinned CLI and run the canary from sibling checkouts:

```bash
cargo build --locked --release \
  --manifest-path ../zed-cli/Cargo.toml \
  --bin zed

python3 scripts/git_submodule_interop.py \
  --zed ../zed-cli/target/release/zed \
  --work-root /tmp/zed-git-submodule-interop
```

The work root must not already exist. It is retained on failure for inspection.
