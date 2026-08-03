# External standalone Nix flake-bundle canary

Tracking: DEN-1422, DEN-1508, DEN-1418, DEN-1411

This repository independently certifies the public standalone-flake renderer from
`zed-pkg/zed-cli`. Here, **Zed** means the independent `zed-pkg` package manager;
it is unrelated to the Zed text editor.

The current reviewed stack is:

- external planner canary pinned to merged planner
  `zed-pkg/zed-cli@19841ece0bf3649a0ed4005c150fb3721d166605`;
- this standalone-bundle canary pinned to renderer candidate
  `zed-pkg/zed-cli@af4c44173ece59b799e77b1e1ed4e35268712b6e` from PR #83; and
- immutable shared contract
  `zed-pkg/zed-interfaces@c2e049006453c26ca8ca291783f681fce75cb01f`.

The canary is intentionally outside the implementation repository. It checks out
one exact CLI commit with persisted Git credentials disabled and injects one
temporary integration test that uses only the public
`zed_cli::nix_export_bundle` and `zed_cli::nix_export_plan` APIs. The temporary
test is removed before source-checkout cleanliness is asserted.

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

On Ubuntu 24.04 and macOS 15, the workflow archives both byte-identical flake
sources and performs one explicit online realization of the exact locked package
while the configured binary cache is available. It then runs `nix flake check`
and `nix build` for both clean-room bundles with `--offline` and
`--no-update-lock-file`. The primed output and both offline output paths must be
identical.

This split is deliberate. An empty Nix store does not contain the immutable
Nixpkgs/stdenv closure. Asking it to build offline immediately tests whether a
hosted runner can bootstrap all of Nixpkgs from source, not whether the generated
Zed package is reproducible or network-independent after its declared closure is
available. The one online realization is therefore a visible input-acquisition
boundary; every replay assertion after that boundary is offline and lock-preserving.

The resulting executable runs with no `zed` command on `PATH`, and the output
closure must not retain a `zed-cli` or `zed-pkg` runtime dependency.

## CI and evidence policy

The workflow has top-level `contents: read`, uses only commit-pinned Actions,
disables persisted checkout credentials, consumes no repository secret, grants
no OIDC or package permission, and never commits or pushes. Successful jobs
retain only non-secret hashes, systems, selected output evidence, and Nix path
information for seven days. Failure diagnostics are retained for three days.

The complete workflow and external Rust consumer are scanned for prohibited write,
secret, OIDC/package-publish, network-client, and process-spawning capabilities.
The denylist tokens are assembled from fragments so the policy code does not
falsely match its own rule declarations.

The candidate pin may be overridden manually only with another exact lowercase
40-character commit. Promotion to a durable baseline requires review and a
follow-up pin to the exact post-merge renderer commit.

## Stack and merge order

This PR is stacked on the external plan canary because a complete Zed → Nix
acceptance path is ordered as:

1. immutable frozen plan;
2. pure deterministic bundle rendering;
3. independently verified persistence and replay;
4. explicit immutable closure acquisition followed by offline Nix replay; and
5. later adapter attestation, overlay/cache publication, and registry storage.

After the external planner canary merges, this branch must be rebuilt on `main`
with exactly five bundle-specific files. After renderer PR #83 merges, the
candidate pin must be advanced to its exact merge commit before this canary can
become a durable baseline.

The canary does not claim that locked dependency-graph assembly, source-builder
inference, overlay publication, signed binary caches, or upstream Nixpkgs
submission are complete. Those remain separately reviewed contracts.
