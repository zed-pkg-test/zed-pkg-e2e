# External standalone Nix flake-bundle canary

Tracking: DEN-1422, DEN-1508, DEN-1418, DEN-1411

This repository independently certifies the public standalone-flake renderer from
`zed-pkg/zed-cli`. Here, **Zed** means the independent `zed-pkg` package manager;
it is unrelated to the Zed text editor.

The canary is intentionally outside the implementation repository. It pins one
exact 40-character `zed-cli` commit, checks out that commit with persisted Git
credentials disabled, and injects one temporary integration test that uses only
the public `zed_cli::nix_export_bundle` and `zed_cli::nix_export_plan` APIs. The
temporary test is removed before source-checkout cleanliness is asserted.

## Contract under test

The external consumer creates a deterministic prebuilt executable artifact and
an explicit strict-v1 export plan, then renders the same bundle twice into two
fresh absolute directories. It requires byte-identical output containing only:

- `flake.nix`;
- the exact immutable `flake.lock`;
- `package.nix`;
- `README.md`;
- the exact Zed artifact;
- canonical `metadata/plan.json`; and
- canonical `metadata/bundle.json`.

The independent Python verifier does not call implementation helpers. It checks:

- the exact strict-v1 file set;
- safe relative paths, regular files, no symlinks, and canonical persisted modes;
- canonical compact JSON;
- sorted, unique inventory paths;
- every recorded file size and SHA-256;
- exact plan and flake-lock digests;
- the domain-separated `zed.nix-flake-bundle/v1` digest;
- byte-identical replay across clean output directories;
- absence of credential and absolute-path canaries; and
- binding between the retained external report and the canonical bundle record.

## Negative coverage

The public renderer must reject:

1. a wrong Zed artifact digest;
2. a mutable Nixpkgs revision;
3. a symbolic-link archive entry;
4. a declared binary without executable mode; and
5. dependency edges that strict bundle v1 does not yet support.

The in-memory validator and the independent persisted verifier separately reject:

1. changed `package.nix`;
2. a missing artifact;
3. an unrecorded extra file;
4. changed `flake.lock`; and
5. noncanonical or modified bundle metadata.

Assertions are not converted to warnings or skipped unsupported behavior.

## Real Nix replay

On Ubuntu 24.04 and macOS 15, the workflow performs one explicit
`nix flake archive --no-update-lock-file` preparation step for each generated
bundle. It then runs `nix flake check` and `nix build` offline with lock updates
disabled. Both clean-room bundles must resolve to the same output path. The
resulting executable runs with no `zed` command on `PATH`, and the output closure
must not retain a `zed-cli` or `zed-pkg` runtime dependency.

## CI and evidence policy

The workflow has top-level `contents: read`, uses only commit-pinned Actions,
disables persisted checkout credentials, consumes no repository secret, grants
no OIDC or package permission, and never commits or pushes. Successful jobs
retain only non-secret hashes, systems, selected output evidence, and Nix path
information for seven days. Failure diagnostics are retained for three days.

The candidate pin may be overridden manually only with another exact lowercase
40-character commit. Promotion to a durable baseline requires review and a
follow-up pin to the exact post-merge implementation commit.

## Stack and merge order

This PR is stacked on the external plan canary because a complete Zed → Nix
acceptance path is ordered as:

1. immutable frozen plan;
2. pure deterministic bundle rendering;
3. independently verified persistence and replay;
4. offline Nix evaluation/build; and
5. later adapter attestation, overlay/cache publication, and registry storage.

The canary does not claim that locked dependency-graph assembly, source-builder
inference, overlay publication, signed binary caches, or upstream Nixpkgs
submission are complete. Those remain separately reviewed contracts.
