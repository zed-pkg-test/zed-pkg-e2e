# Native registry contract canary

This canary consumes the public `zed-interfaces` native-registry API from a
separate repository. It is intentionally independent of npm, crates.io,
credentials, and publication side effects.

## Certified boundary

For one exact `zed-interfaces` commit, the workflow proves that:

1. the complete interface crate compiles with its locked dependency graph;
2. the native-registry JSON Schema can be generated twice with identical bytes;
3. the checked-in Rust tests and schema-contract test pass;
4. rustfmt and Clippy with warnings denied pass;
5. an external crate can construct and validate a generic npm wrapper with
   Linux/musl and macOS platform packages; and
6. the public helpers identify SemVer versions that differ only in build
   metadata as the same precedence identity.

The consumer keeps architecture and libc outside the version. It uses one
`1.4.2` API version for the wrapper and both platform packages, with exact
artifact SHA-256, size, and format on each publication.

## Isolation

`.github/workflows/native-registry-contract.yml` pins the interface candidate to
a full 40-character commit, checks it out without persisted credentials, and
runs on a clean hosted runner. After the full crate build warms the Cargo cache,
the external consumer is compiled and executed offline. The generated schema,
checksum, resolved consumer lock, and source-tree status are uploaded as an
evidence bundle.

## Updating the pin

Change `ZED_INTERFACES_REF` only to a reviewed commit that contains the complete
contract, generated schema registration, and tests. The pull request must show
the exact certified commit and workflow run before the pin is promoted to
`main`.
