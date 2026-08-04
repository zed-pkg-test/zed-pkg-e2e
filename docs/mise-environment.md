# mise environment certification

This suite certifies the project-local mise interoperability surface implemented by `zed-pkg/zed-cli`.

`zed-pkg` is an independent multi-language package manager and has no relationship with the Zed editor. Here, `zed` means the `zed-pkg` CLI.

## Scope

The workflow `.github/workflows/mise-environment.yml` builds immutable `zed-cli` commit `4186d50562b62b52e0e13d2a4b0431bbaf7c0a2b` against immutable `zed-interfaces` commit `c2e049006453c26ca8ca291783f681fce75cb01f`, then runs the same black-box suites on:

- Ubuntu 24.04 x64;
- macOS 15 arm64; and
- Windows Server 2025 x64.

Both revisions are full 40-character pins. A pull request that updates the certified CLI must update the pin explicitly and prove the new commit across the complete matrix.

The CLI candidate is the clean static adapter in `zed-pkg/zed-cli#91`, stacked on the one-file Windows warning fix in `zed-pkg/zed-cli#92`. PR #91 contains only the static adapter, typed CLI and dispatcher integration, tests, flags metadata, and documentation relative to that prerequisite. Runtime `zed dev --mise` composition is already merged independently through `zed-pkg/zed-cli#70` and is documented alongside this read-only surface.

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

## Windows prerequisite found by this suite

The first exact-pinned Windows run passed all static environment unit and integration tests but failed Clippy because `secure_file_mode(path: &Path)` used `path` only inside `#[cfg(unix)]`. PR #92 renames that parameter to `_path` without changing behavior. The current immutable candidate includes that prerequisite so Windows continues to enforce `-D warnings` rather than weakening the platform gate.

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

1. PR #92 is green and merged;
2. PR #91 is retargeted from the #92 branch back to `main` without changing the candidate tree;
3. all three operating-system jobs and the aggregate gate pass on the exact commit above;
4. ordinary `zed-cli` install, publish, R2G, development-shell, policy, and repository-hardening checks are green;
5. the CLI and test pull requests cross-link their Linear issue and exact commit SHAs; and
6. legacy static adapter PR #38 is closed as superseded by the cleaner current-main PR #91.
