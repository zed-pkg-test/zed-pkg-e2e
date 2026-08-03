# Native dependency provenance lockfile canary

This canary proves that exact source-aware npm/Cargo resolutions can be stored in
`.zpkg.lock`, read back without reinterpretation, and consumed by the current
Zed CLI interface boundary.

## Exact lockfile behavior

The external Rust consumer verifies:

- legacy lockfile version 1 documents remain readable with no native entries;
- npm and Cargo exact locks serialize as `[[native-dependency]]` tables;
- the original declaration, canonical requirement, exact version, digest, size,
  and format round trip;
- insertion order does not change emitted TOML;
- npm and Cargo packages with the same textual name remain separate identities;
- upsert replaces only `(registry, package.name)`;
- duplicate native identities fail during write and parse;
- requirement-receipt drift fails during upsert; and
- malformed artifact identity fails before serialization.

## Interface gates

The workflow pins one exact `zed-interfaces` commit and requires:

- rustfmt check;
- checked-in lockfile JSON Schema drift rejection;
- complete locked interface target tests;
- Clippy with warnings denied; and
- an external consumer built and executed offline after the interface build.

## Current CLI compatibility

The canary also pins the current reviewed `zed-cli` commit. In a disposable
checkout it replaces only the `zed-interfaces` Git dependency with the exact
candidate path, then runs `cargo check --all-targets` and
`cargo test --all-targets --no-run`.

This makes the additive serialized change and the Rust source-level field
addition explicit. No CLI source changes are committed by the canary.

## Isolation

- immutable 40-character interface and CLI pins;
- read-only repository permissions;
- no persisted checkout credentials;
- immutable third-party Action pins;
- no registry publication or credentials; and
- evidence containing schema digest, consumer lock, exact commits, CLI path
  dependency patch, and source-tree status.

## Stack

The canary branch is stacked on the fully certified native dependency range and
lifecycle repair branches. After those bases land, retarget this PR to `main`
without changing the exact product commit or consumer behavior.
