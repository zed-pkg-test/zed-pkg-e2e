# Native registry contract canary

This canary consumes the public `zed-interfaces` native-registry API from a
separate repository. It is intentionally independent of npm, crates.io,
credentials, and publication side effects.

## Exact product candidate

The workflow certifies:

```text
zed-pkg/zed-interfaces@56772c0af6b90d69df47f0eca8263ce735d37f30
```

That commit semantically combines the current manifest native-dependency and
install-hook contract with the immutable native-registry publication model.

## Certified boundary

For the exact commit above, the workflow proves that:

1. the complete interface crate compiles with its locked dependency graph;
2. the native-registry JSON Schema can be generated twice with identical bytes;
3. checked-in Rust tests and the schema-contract test pass;
4. rustfmt and Clippy with warnings denied pass;
5. an external crate can construct and validate a generic npm wrapper with
   Linux/musl and macOS platform packages;
6. a portable Cargo publication remains valid;
7. the public helpers identify SemVer versions that differ only in build
   metadata as the same precedence identity; and
8. a second external crate proves the publication-family topology rules:
   at most one portable package, a meta package must select at least one
   platform, every platform must be selected exactly once when a meta package
   exists, and platform-only publication families remain valid.

The consumer keeps architecture and libc outside the version. It uses one
`1.4.2` API version for the wrapper and both platform packages, with exact
artifact SHA-256, size, and format on each publication.

## Isolation

`.github/workflows/native-registry-contract.yml` pins the interface candidate to
a full 40-character commit, checks it out without persisted credentials, and
runs on a clean hosted runner with read-only repository permission. After the
full crate build warms the Cargo cache, both external consumers are compiled and
executed offline. The generated schema, checksum, resolved consumer lock, exact
product commit, and source-tree status are uploaded as an evidence bundle.

## Updating the pin

Change `ZED_INTERFACES_REF` only to a reviewed commit containing the complete
contract, generated schema registration, checked-in schema, and tests. The pull
request must show the exact certified commit and successful workflow run before
the pin is promoted to `main`.
