# mise environment certification

This suite certifies the project-local mise interoperability surface implemented by `zed-pkg/zed-cli`.

`zed-pkg` is an independent multi-language package manager and has no relationship with the Zed editor. Here, `zed` means the `zed-pkg` CLI.

## Scope

The workflow `.github/workflows/mise-environment.yml` builds immutable `zed-cli` commit `93ff1cce4a0ed1a29a8172e1ba01c1cc12fddd01` against immutable `zed-interfaces` commit `c2e049006453c26ca8ca291783f681fce75cb01f`, then runs the same black-box suites on:

- Ubuntu 24.04 x64;
- macOS 15 arm64; and
- Windows Server 2025 x64.

Both revisions are full 40-character pins. A pull request that updates the certified CLI must update the pin explicitly and prove the new commit across the complete matrix.

The CLI candidate is the clean static adapter in `zed-pkg/zed-cli#91`. It semantically includes the two Windows portability prerequisites already merged to `main`:

- `zed-pkg/zed-cli#92`, which keeps the auth permission helper warning-free on Windows;
- `zed-pkg/zed-cli#94`, which gates Unix-only shell/executable test fixtures without changing production behavior.

Relative to updated `main`, PR #91 still contains only the static adapter, typed CLI/dispatcher integration, tests, flags metadata, README, and documentation. Runtime `zed dev --mise` composition is already merged independently through `zed-pkg/zed-cli#70` and is documented alongside this read-only surface.

## Certified behavior

`scripts/mise_environment.py` exercises the real compiled `zed` executable. It verifies:

- `zed env import mise` and `zed env verify mise --frozen`;
- exact config/lock coverage and stable machine-readable output;
- semantic digest stability across TOML key/table ordering and presentation changes;
- digest drift when locked artifact provenance changes;
- SHA-256 lock metadata, malformed-checksum rejection, and missing-checksum rejection;
- no parent or user-global mise configuration leakage;
- no dependency on an ambient `mise` executable—the child process receives an empty `PATH`;
- no mutation of `mise.toml`, `mise.lock`, or the project tree;
- fail-closed ambiguous-config, missing-lock, and config/lock-drift behavior;
- `.tool-versions` authoring import and frozen-lock rejection; and
- project-root escape rejection through symlink canonicalization where the host permits symlink creation.

`scripts/mise_lock_naming.py` independently certifies mise's current normalized adjacent-lock convention through implicit and explicit real-CLI calls:

```text
mise.toml       -> mise.lock
.mise.toml      -> mise.lock
mise.test.toml  -> mise.test.lock
```

It proves that `.mise.toml` discovers `mise.lock`, that the JSON result reports the normalized lock path, and that a legacy `.mise.lock`-only project fails closed rather than being silently accepted.

The harnesses intentionally do not install runtimes or execute mise tasks, hooks, templates, plugins, or environment expressions. Those are separate compatibility and trust surfaces.

## Platform defects found by this suite

The Windows matrix passed the actual static-mise unit and real CLI tests, then exposed two existing current-main portability defects under strict all-target Clippy:

1. an auth helper parameter used only on Unix;
2. Unix-only develop-shell helpers and shell-script executable fixtures compiled on Windows.

PRs #92 and #94 isolate and merge those repairs without weakening `-D warnings` or changing product behavior. This final candidate contains both prerequisite trees.

## Local execution

Build the exact `zed-cli` revision being certified, then run:

```bash
python scripts/mise_environment.py \
  --zed ../zed-cli/target/release/zed \
  --work-root /tmp/zed-mise-environment

python scripts/mise_lock_naming.py \
  --zed ../zed-cli/target/release/zed \
  --work-root /tmp/zed-mise-lock-naming
```

Each work root must not already exist. On success, each harness emits a JSON certification summary. On failure, it reports the exact failing command or invariant; GitHub Actions also uploads the disposable work roots and toolchain diagnostics.

## Promotion rule

Promote and merge this certification only after:

1. all three operating-system jobs and the aggregate gate pass on `93ff1cce4a0ed1a29a8172e1ba01c1cc12fddd01`;
2. ordinary `zed-cli` CI, publish/R2G, development-shell, policy, Nix, OCI, and repository-hardening checks are green at that same head;
3. ordinary lifecycle, source-map, recursive-install, browser, install-boundary, and runtime E2E checks are green on this PR head;
4. the CLI and test pull requests cross-link their Linear issues and exact commit SHAs; and
5. legacy static adapter PR #38 is closed as superseded by current-main PR #91.
