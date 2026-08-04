# mise runtime certification

This suite independently certifies the consumer-visible runtime composition added to `zed-pkg/zed-cli` by DEN-1420.

`zed-pkg` is an independent multi-language package manager and has no relationship with the Zed editor. In this document, `zed` means the `zed-pkg` CLI.

## Relationship to the static mise suite

The draft static suite in `zed-pkg-e2e#7` verifies deterministic import, export, lock parsing, normalized digests, drift detection, and project-boundary behavior **without executing mise**.

This runtime suite exercises a separate contract: `zed dev --mise auto|never|required` invoking `mise exec`, then composing the activated toolchain with Zed package restoration, `zed_modules/.bin`, and project-local mutable caches.

Neither suite may substitute for the other.

## Lifecycle baseline

The canonical shared-schema lifecycle/source-map correction from `zed-pkg-e2e#24` is merged on `main` as `636fbe87aa26cf25a1c22d8f64383d4b5b8e6da8`.

That baseline classifies `shared-schema` as a package fixture, maps `zedtest/shared-schema`, removes only the exact untracked publish archive with fail-closed checks, and passed the complete 22-fixture lifecycle matrix. This runtime suite is now based directly on `main`; it does not duplicate or carry a private copy of the lifecycle repair.

## Immutable candidate

`.github/workflows/mise-runtime.yml` builds a full 40-character `zed-cli` commit pin and runs the same black-box harness on Ubuntu 24.04 and macOS 15. The workflow does not install mise or download language runtimes. Instead, it supplies a deterministic executable stub whose arguments and relevant environment values are observable.

The current candidate is `854b55c348307d00e2fd2ec8309792c249208192`, the current-main integration head of `zed-pkg/zed-cli#70`. That head preserves the six reviewed mise runtime paths while incorporating the merged recursive installer, strict frozen lock/fetch, Nix export/fetch/flake, OCI, and related current-main history. Update the pin whenever that pull request changes, and never promote this certification against a branch name or abbreviated SHA.

## Certified behavior

`scripts/mise_runtime.sh` proves:

- `--mise required` enters `mise exec` exactly once;
- mise-selected tools and `zed_modules/.bin` are both available to the child command;
- noninteractive POSIX command execution preserves the composed PATH instead of starting a login shell that can rewrite it, including on macOS;
- `ZED_DEV_MISE` supplies the flags-to-environment fallback while an explicit CLI flag wins;
- `auto` falls back to the native Zed environment when no project configuration exists;
- `required` fails closed when configuration or the mise executable is missing;
- frozen runtime composition requires adjacent `mise.lock` evidence;
- frozen mode enables locked operation, ignores `.tool-versions`, and isolates user/system mise configuration beneath `.zed/dev/mise`;
- frozen integrated mode rejects ambient mise activation because the parent environment's config and lock cannot be proven by Zed;
- `mise.toml` has deterministic precedence when both supported config names exist;
- `.mise.toml` uses the same adjacent `mise.lock` contract;
- configuration discovery cannot escape the owning Git checkout;
- Zed and mise activation markers prevent recursive re-entry in non-frozen mode; and
- the exact child exit status survives both `mise exec` and `zed dev`.

The workflow also runs all three direct command-shell test targets: `develop_edge_cases`, `develop_shell_edge_cases`, and `develop_help_contract`. This keeps the source-level Bash/Fish/PowerShell/cmd/generic dispatch matrix synchronized with the black-box PATH assertions.

The first runtime assertion is a regression test for the macOS PATH failure that exposed the former `bash -lc` behavior. It requires both a mise-selected executable and a project-local Zed executable to remain callable in the expected order.

## Local execution

Build the exact candidate and run:

```bash
cargo build --locked --release --manifest-path ../zed-cli/Cargo.toml --bin zed
bash scripts/mise_runtime.sh \
  --zed ../zed-cli/target/release/zed \
  --work-root /tmp/zed-mise-runtime
```

The work root must not already exist. On success, the harness emits TAP-style assertions followed by a machine-readable summary. On failure, an ERR trap identifies the failing line and command; GitHub Actions also uploads the disposable project tree and toolchain diagnostics.

## Promotion gate

Keep this pull request draft until:

1. the pull request is based directly on current `main` containing merged PR #24;
2. `zed-pkg/zed-cli#70` has a final immutable current-main integration head;
3. the workflow and documentation pins match that head exactly;
4. Ubuntu and macOS runtime jobs plus the aggregate gate pass on that pin;
5. ordinary lifecycle, install/OCI, browser, and policy checks pass on the retargeted pull request;
6. the static suite in `zed-pkg-e2e#7` remains green for its own declared surface;
7. ordinary `zed-cli` CI, development-shell, policy, hardening, Nix, OCI, and formal-review checks are green; and
8. DEN-1420 and DEN-1449 link both pull requests and their exact candidate SHA.
