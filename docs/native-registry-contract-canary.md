# Native registry contract canary

This canary consumes the public `zed-interfaces` native-registry API from a
separate repository. It is intentionally independent of npm, crates.io,
credentials, and publication side effects.

## Certified candidate

The workflow pins the exact current-main interface candidate:

```text
a6aaad6911c845be7d8069d67f9bef82c85452b7
```

That candidate preserves the merged native dependency/install-hook interface
surface while adding the finalized immutable native-registry publication
contract and its final review documentation. It contains no branch-writing or
temporary finalizer workflow.

## Certified boundary

For the immutable candidate, the workflow proves that:

1. the complete interface crate compiles with its locked dependency graph;
2. the native-registry JSON Schema can be generated twice with identical bytes;
3. the checked-in Rust tests and schema-contract test pass;
4. rustfmt and strict Clippy pass over every target;
5. an external crate can construct and validate a generic npm wrapper with
   Linux/musl and macOS platform packages;
6. a portable Cargo publication validates independently;
7. presentation order does not alter canonical adapter bytes;
8. SemVer versions differing only in build metadata collide at the native
   publication boundary; and
9. publication-family topology fails closed for duplicate portable/meta roles,
   empty or incomplete meta selections, dangling or mismatched edges, and
   duplicate platform identity.

Platform-only families are also certified when consumers select native packages
directly without a generic wrapper.

The consumer keeps architecture and libc outside the version. It uses one
`1.4.2` API version for the wrapper and every platform package, with exact
artifact SHA-256, size, and format on each publication.

## Isolation

`.github/workflows/native-registry-contract.yml` pins the interface candidate to
a full 40-character commit, checks it out without persisted credentials, and
runs on a clean Ubuntu 24.04 hosted runner. After the full crate build warms the
Cargo cache, the external consumer is compiled and executed offline. The
generated schema, checksum, resolved consumer lock, exact candidate SHA, and
source-tree status are uploaded as a bounded evidence bundle.

The canary performs no registry mutation and requires no npm, crates.io, OCI,
Zed registry, or other publication credential.

## Updating the pin

Change `ZED_INTERFACES_REF` only to a reviewed commit that contains the complete
contract, generated schema registration, topology hardening, and tests. The
pull request must show the exact certified commit and terminal-successful
workflow set before the pin is promoted to `main`.
