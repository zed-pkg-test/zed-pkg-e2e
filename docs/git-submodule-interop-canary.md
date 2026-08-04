# Git-submodule interoperability canary

This canary independently certifies the mixed Git/Zed submodule behavior in
`zed-pkg/zed-cli#105`. It runs outside the product repository against one exact
immutable CLI commit:

```text
zed-pkg/zed-cli@01bf4184f228262d75661f393366a5adbeddee6e
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
- compiles the Python canary;
- checks both repositories with `git diff --check`;
- runs product `rustfmt`, the focused real-binary integration test, and
  all-target Clippy with warnings denied;
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
