# mise frozen identity certification

This suite certifies the next read-only mise parity slice after the merged Tier-1 import/verify contract.

`zed-pkg` is an independent multi-language package manager and is unrelated to the Zed editor. Here, `zed` means the `zed-pkg` CLI.

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

## Deliberate boundary

This suite does not claim complete current `mise.lock` parity. It does not yet certify option-dependent multiple identities, `install`, `url_api`, verified provenance, additional artifacts, shared conda/pkgx dependency sections, deterministic export, or import/export/import round trips. Those remain tracked by DEN-1461 and the remaining DEN-1481 work.

## Promotion gate

The final workflow must pin a full 40-character `zed-cli` commit and run on Ubuntu 24.04, macOS 15, and Windows Server 2025. Promotion requires:

1. the implementation PR's exact-head repository workflows to be green;
2. this black-box harness and the existing static mise harnesses to pass on all three platforms;
3. strict all-target Clippy with warnings denied;
4. ordinary lifecycle, recursive-install, browser, install-boundary, and runtime-mise workflows to remain green; and
5. exact implementation and certification commits to be recorded in DEN-1481, DEN-1461, and the canonical mise policy document.
