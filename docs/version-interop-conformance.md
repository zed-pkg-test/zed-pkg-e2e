# Version and native-publication interoperability corpus

This contract converts the strict native-registry rules in
`zed-pkg/zed-interfaces` into reusable data plus an external Rust consumer. It
is stacked on the independent current-main native-registry contract canary so
future CLI, server, SDK, lockfile, and publication-plan work can reuse one
reviewed set of edge cases.

## Immutable product pin

The corpus certifies exactly:

```text
zed-pkg/zed-interfaces@032b52f6e335e5696cb793d6a955c8f0658a95eb
```

The same 40-character commit appears in `cases.json`, the Rust consumer, and the
workflow environment. CI rejects disagreement among those pins.

## Data sets

`fixtures/version-interop-corpus/cases.json` contains:

- three SemVer precedence projections with build metadata removed;
- four collision and non-collision pairs;
- a valid npm generic-wrapper family with Linux/ARM64/musl and Darwin/ARM64
  packages;
- a valid portable Cargo package; and
- nineteen fail-closed mutations.

The negative corpus covers schema/version drift, malformed native names,
platform-selector topology, meta-to-platform references, duplicate identities,
artifact digest shape, and zero-byte artifacts.

## Assertions

The external consumer proves that:

1. build metadata never differentiates precedence identity;
2. prerelease identity still participates in precedence;
3. valid npm and Cargo records survive canonical JSON round trips;
4. publication and platform-selector ordering do not change canonical bytes;
5. each negative mutation returns the exact expected public error class; and
6. the corpus cannot shrink without an explicit source change.

## Isolation

The workflow checks out the product commit without persisted credentials, builds
and tests the complete interface crate, generates a fixture lockfile, and then
runs the external consumer offline. Repository permissions are read-only and all
third-party Actions use immutable commits. No npm, crates.io, OCI, or Zed
registry is contacted or mutated.

Evidence includes the checked corpus, its SHA-256, the generated consumer lock,
the exact product commit, command output, and clean-tree status.

## Current-main relationship

The current replacement is based on the same native-registry consumer branch as
`zed-pkg-test/zed-pkg-e2e#51`. The original reviewed corpus history remains a
merge parent, while the five-file review delta is carried onto the latest E2E
baseline and repinned to the current product integration head. The complete
browser, fixture lifecycle, recursive, mise, Git-submodule, and source-map
workflow set remains a promotion gate in addition to this focused corpus job.

## Local shape

With `zed-interfaces` checked out as a sibling of the harness repository:

```sh
cargo test --locked --manifest-path ../zed-interfaces/Cargo.toml --all-targets
cargo generate-lockfile --offline \
  --manifest-path fixtures/version-interop-corpus/Cargo.toml
cargo run --locked --offline \
  --manifest-path fixtures/version-interop-corpus/Cargo.toml
```

The fixture is not a publisher. It validates the shared planning and identity
boundary that a later credential-bearing `apply` implementation must recheck.
