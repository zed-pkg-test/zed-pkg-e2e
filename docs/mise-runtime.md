# mise runtime certification

This suite independently certifies the consumer-visible runtime composition added to `zed-pkg/zed-cli` by DEN-1420.

`zed-pkg` is an independent multi-language package manager and has no relationship with the Zed editor. In this document, `zed` means the `zed-pkg` CLI.

## Relationship to the static mise suite

The draft static suite in `zed-pkg-e2e#7` verifies deterministic import, export, lock parsing, normalized digests, drift detection, and project-boundary behavior **without executing mise**.

This runtime suite exercises a separate contract: `zed dev --mise auto|never|required` invoking `mise exec`, then composing the activated toolchain with Zed package restoration, `zed_modules/.bin`, and project-local mutable caches.

Neither suite may substitute for the other.

## Immutable candidate

`.github/workflows/mise-runtime.yml` builds a full 40-character `zed-cli` commit pin and runs the same black-box harness on Ubuntu 24.04 and macOS 15. The workflow does not install mise or download language runtimes. Instead, it supplies a deterministic executable stub whose arguments and relevant environment values are observable.

The default candidate is the exact head of `zed-pkg/zed-cli#70`. Update the pin whenever that pull request changes, and never promote this certification against a branch name or abbreviated SHA.

## Certified behavior

`scripts/mise_runtime.sh` proves:

- `--mise required` enters `mise exec` exactly once;
- mise-selected tools and `zed_modules/.bin` are both available to the child command;
- `ZED_DEV_MISE` supplies the flags-to-environment fallback while an explicit CLI flag wins;
- `auto` falls back to the native Zed environment when no project configuration exists;
- `required` fails closed when configuration or the mise executable is missing;
- frozen runtime composition requires adjacent `mise.lock` evidence;
- frozen mode enables locked operation, ignores `.tool-versions`, and isolates user/system mise configuration beneath `.zed/dev/mise`;
- `mise.toml` has deterministic precedence when both supported config names exist;
- `.mise.toml` uses the same adjacent `mise.lock` contract;
- configuration discovery cannot escape the owning Git checkout;
- Zed and mise activation markers prevent recursive re-entry; and
- the exact child exit status survives both `mise exec` and `zed dev`.

The harness also checks that project-local Zed binaries remain ahead of inherited PATH entries while a mise-selected tool remains callable.

## Local execution

Build the exact candidate and run:

```bash
cargo build --locked --release --manifest-path ../zed-cli/Cargo.toml --bin zed
bash scripts/mise_runtime.sh \
  --zed ../zed-cli/target/release/zed \
  --work-root /tmp/zed-mise-runtime
```

The work root must not already exist. On success, the harness emits TAP-style assertions followed by a machine-readable summary. On failure, it prints the exact failing assertion; GitHub Actions also uploads the disposable project tree and toolchain diagnostics.

## Promotion gate

Keep this pull request draft until:

1. `zed-pkg/zed-cli#70` has a final immutable head;
2. the workflow pin matches that head exactly;
3. Ubuntu and macOS runtime jobs pass on that pin;
4. the static suite in `zed-pkg-e2e#7` remains green for its own declared surface;
5. ordinary `zed-cli` CI, development-shell, policy, and hardening checks are green; and
6. DEN-1420 and DEN-1449 link both pull requests and their exact candidate SHA.
