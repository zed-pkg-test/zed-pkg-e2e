# mise environment certification

This suite certifies the project-local mise interoperability surface implemented by `zed-pkg/zed-cli`.

`zed-pkg` is an independent multi-language package manager and has no relationship with the Zed editor. Here, `zed` means the `zed-pkg` CLI.

## Scope

The workflow `.github/workflows/mise-environment.yml` builds one immutable `zed-cli` commit and runs the same black-box harness on:

- Ubuntu 24.04 x64;
- macOS 15 arm64; and
- Windows Server 2025 x64.

The `zed-cli` and `zed-interfaces` revisions are full 40-character commit pins. A pull request that updates the certified CLI must update the pin explicitly and prove the new commit across the complete matrix.

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

The harness intentionally does not install runtimes or execute mise tasks, hooks, templates, plugins, or environment expressions. Those are separate compatibility and trust surfaces.

## Local execution

Build the exact `zed-cli` revision being certified, then run:

```bash
python scripts/mise_environment.py \
  --zed ../zed-cli/target/release/zed \
  --work-root /tmp/zed-mise-environment
```

The work root must not already exist. On success, the harness emits one JSON certification summary. On failure, it reports the exact command, working directory, exit status, stdout, and stderr.

## Promotion rule

This certification pull request remains draft while its pinned `zed-cli` pull request is draft. Promote and merge it only after:

1. the CLI commit is final and immutable;
2. all three operating-system jobs pass on that exact commit;
3. ordinary `zed-cli` install, publish, R2G, development-shell, policy, and repository-hardening checks remain green; and
4. the CLI and test pull requests cross-link their Linear issue and exact commit SHAs.
