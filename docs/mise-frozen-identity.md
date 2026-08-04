# mise frozen identity certification

This suite certifies the next read-only mise parity slice after the merged Tier-1 import/verify contract.

`zed-pkg` is an independent multi-language package manager and is unrelated to the Zed editor. Here, `zed` means the `zed-pkg` CLI.

## Immutable candidate

Implementation PR: `zed-pkg/zed-cli#104`.

The workflow builds and tests exact CLI commit:

```text
41b7a228377f39aa07527725ee4439c1d153997e
```

The commit is a semantic two-parent composition of the reviewed frozen-identity product tree and then-current `main`. The implementation PR contains only:

- `src/environment.rs`;
- `tests/mise_environment_cli.rs`;
- `docs/mise-interop.md`; and
- the permanent read-only `.github/workflows/mise-frozen-identity.yml` matrix.

## Scope

`scripts/mise_frozen_identity.py` runs the real immutable CLI with an empty `PATH` and verifies that frozen project-local mise state fails closed when its declared identity is incomplete or inconsistent.

The certified checks are:

- every applicable platform requested through `settings.lockfile_platforms` exists for every configured tool;
- a tool-level `os` constraint limits which requested platforms apply;
- a backend-qualified configuration such as `aqua:jqlang/jq` equals the lock's backend identity;
- the resolved lock version satisfies the authored exact, boundary-prefix, or supported SemVer-range requirement;
- a plain prefix such as `22` accepts `22.4.0` but rejects `220.0.0`;
- unsupported manager-specific range syntax fails closed rather than being approximated;
- successful and failing verification remain read-only; and
- no ambient mise executable or global configuration is required.

The existing static-mise baseline was also strengthened: every tool now carries every globally requested platform so semantic-digest, provenance-drift, checksum, boundary, and mutation tests remain valid under the stricter completeness rule.

## Cross-platform workflow

`.github/workflows/mise-environment.yml` pins the full candidate SHA above and runs on:

- Ubuntu 24.04;
- macOS 15; and
- Windows Server 2025.

Each platform requires formatting, environment unit tests, the complete real CLI integration target, all-target Clippy with warnings denied, a locked release build, the original static import harness, normalized lock-naming certification, and the new frozen-identity harness. Failure diagnostics preserve all three disposable work roots plus toolchain and checkout state.

## Deliberate boundary

This suite does not claim complete current `mise.lock` parity. It does not yet certify option-dependent multiple identities, `install`, `url_api`, verified provenance, additional artifacts, shared conda/pkgx dependency sections, deterministic export, or import/export/import round trips. Those remain tracked by DEN-1461 and the remaining DEN-1481 work.

## Promotion gate

Promotion requires:

1. every exact-head implementation workflow on `41b7a228377f39aa07527725ee4439c1d153997e` to pass;
2. this black-box harness and the existing static mise harnesses to pass on all three platforms;
3. strict all-target Clippy with warnings denied;
4. ordinary lifecycle, recursive-install, browser, install-boundary, and runtime-mise workflows to remain green;
5. the E2E workflow pin to match the implementation head exactly; and
6. exact implementation and certification commits to be recorded in DEN-1481, DEN-1461, and the canonical mise policy document.
