# Native dependency provenance lockfile canary

This canary proves that exact source-aware npm/Cargo resolutions can be stored in
`.zpkg.lock`, read back without reinterpretation, and consumed by the primary
Rust applications that share `zed-interfaces`.

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

## Primary Rust consumer compatibility

The canary pins exact reviewed commits for:

- `zed-cli` — `7e5ac1897a872223f8316ef1c3342a2fa1982504`;
- `zed-api-server.rs` — `3d2d4c98024e5bf718dc79c2648bd10d7759a31c`; and
- `zed-web-server.rs` — `b669850d317ebc07a8960aea4014706c55ffad22`.

For zed-cli, a disposable checkout replaces only the Git-based
`zed-interfaces` dependency with the exact candidate path, then runs
`cargo check --all-targets` and `cargo test --all-targets --no-run`.

The API and web repositories already use sibling path dependencies, so their
exact checkouts are placed next to the candidate and compiled with all targets.
The API workspace includes its migration crate. No consumer source changes are
committed by the canary.

This makes the additive serialized change and the Rust source-level field
addition explicit across the CLI, API, web server, and an independent external
consumer.

## Isolation

- immutable 40-character interface and consumer pins;
- read-only repository permissions;
- no persisted checkout credentials;
- immutable third-party Action pins;
- no registry publication or credentials; and
- evidence containing schema digest, consumer lock, exact commits, CLI path
  dependency patch, and source-tree status for every checked-out consumer.

## Stack

The native dependency range canary branch contains its reviewed lifecycle repair
as a real merge parent rather than only as pull-request metadata. The exact
inherited base commit is
`61a1216ee6ef2e2c7fc95bed2c6e4160e384cf4d`, whose parents are the range canary
head and lifecycle repair head.

The lockfile branch then promoted its own previously tested synthetic merge into
real history at `e9166360507d173cc6a434ca8c5fd64cf354b007`. Final workflows run
on descendants of that commit, so source-map, native-range, lockfile, and all
consumer compatibility checks exercise one concrete branch tree.

After the stacked bases land, retarget this PR to `main` without changing the
exact product commit or consumer behavior.
