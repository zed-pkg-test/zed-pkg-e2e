# Terminal-context certification

This repository independently certifies the terminal/TTY/shell context behavior owned by `zed-pkg/zed-cli`.

Canonical production ownership:

- Linear: `DEN-2591`
- implementation: `zed-pkg/zed-cli#219`
- shipped merge: `ad6ff369a763f0fbdc3b677894655c73b23e062c`
- companion flags2env API: `DEN-2581` / `ORESoftware/flags-2-env#24`
- policy documentation: `zed-pkg/zed-docs#55`
- test-org acceptance: `DEN-2925` / `zed-pkg-test/zed-pkg-e2e#123`

## Why this lives in the test organization

The production repository already has unit, Docker, macOS, and Windows coverage. This test-org gate is deliberately independent: it checks out an immutable shipped candidate, builds that candidate on native GitHub runners, and drives the resulting executable as an external consumer would.

A failure here is not fixed by adding a second terminal detector to the test harness. The defect is filed against the owning production repository, fixed there, and then re-certified here.

## Platform matrix

The workflow runs on:

- Ubuntu 24.04;
- macOS 15;
- Windows Server 2025.

Every lane checks the exact product Git commit before testing.

## Acceptance contract

The workflow runs the production `terminal_context` and `interactive` Rust unit contracts and then exercises the built CLI through the real `zed init` mutation checkpoint in a disposable test-org-owned working directory.

`zed init` is intentionally used instead of registry installation: terminal-context certification should not depend on registry transport, artifact paths, network access, or package materialization. The first Windows run exposed an unrelated `file:///D:/...` registry-path defect before any prompt assertion ran; that transport defect is tracked separately rather than weakening or conflating this certification.

The black-box assertions are:

1. Piped `yes` is not human consent in ordinary CI/non-TTY execution.
2. Forced terminal stdin and stderr do not bypass CI when CI is forced true.
3. `TERM=dumb` remains fail-closed even with terminal state forced true and CI cleared.
4. The documented `ZED_PKG_FORCE_*` controls can explicitly simulate a safe terminal for deterministic testing.
5. The shared `F2E_FORCE_*` spellings produce the same accepted behavior without Zed-specific force variables present.
6. stdout may remain redirected while stderr carries the interactive checkpoint.
7. A rejected checkpoint leaves `.zpkg.toml` absent.
8. An accepted checkpoint creates the requested manifest with the expected package identity.
9. Linux and macOS additionally exercise the production `forkpty()` harness with a real pseudo-terminal; Windows uses the deterministic override path because that upstream helper is Unix-specific.

## Security and permissions

The certification workflow uses only `contents: read`. It does not require GitHub PATs, Linear tokens, Cloudflare credentials, registry credentials, or any other secret. The mutation target is a temporary runner directory and never a checked-out repository.

The workflow must never copy user-supplied credentials into source, logs, artifacts, issue bodies, or environment variables.