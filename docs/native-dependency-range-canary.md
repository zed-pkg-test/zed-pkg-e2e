# Native dependency range and exact-lock canary

This canary certifies the public `zed-interfaces` contract that translates npm
and Cargo dependency declarations before freezing one exact artifact identity.
It does not invoke npm, Cargo, crates.io, the npm registry, Nix, or another
resolver.

## Immutable candidate

The workflow pins the complete stacked product head:

```text
zed-pkg/zed-interfaces@859a71ccd4406f942733fd3bae842a21edff24b2
```

That candidate contains the current-main native-registry publication contract
plus the source-aware native dependency requirement and exact-lock layer. A
branch name, abbreviated commit, or moving tag is never accepted as the product
input.

## Source-aware behavior

The same declaration is deliberately interpreted through the selected native
registry:

- npm `1.2.3` becomes exact `=1.2.3`;
- Cargo `1.2.3` becomes default caret `^1.2.3`;
- npm `1.2` becomes x-range `1.2.*`; and
- Cargo `1.2` remains default caret `^1.2`.

The external consumer also exercises comparator intersections, wildcards,
major-zero caret behavior, explicit prereleases, and highest-satisfying
selection independent of candidate order.

Npm partial inequality comparators receive their native boundaries: `>1`
canonicalizes to `>=2.0.0`, `>1.2` to `>=1.3.0`, and `<=1.2` to `<1.3.0`.
Whitespace between an operator and its version is covered for npm and Cargo;
Cargo still requires commas between multiple comparator clauses.

## Frozen lock boundary

A successful resolution records the original declaration, deterministic
canonical requirement, exact strict-SemVer package version, lowercase nonzero
artifact SHA-256, size, and archive format. The public validator recomputes the
translation and rejects canonical-requirement drift, a resolved version outside
the range, malformed artifact identity, duplicate candidates, and SemVer build
metadata.

Npm requirements and exact candidate identities are also constrained to numeric
components representable exactly by JavaScript. Leading-zero partials and
components above `Number.MAX_SAFE_INTEGER` are rejected to avoid a Rust/npm
interpretation split.

## Unsupported syntax

The v1 canary requires fail-closed rejection for unions, hyphen ranges, npm
dist-tags and aliases, workspace/local/Git/URL sources, npm comma syntax, Cargo
whitespace-only comparator intersections, and npm-style Cargo `x` wildcards.
No rejected syntax may silently degrade to an opaque exact string.

## Reproducibility and isolation

The workflow:

- requires one exact 40-character `zed-interfaces` commit;
- checks out all sources without persisted credentials;
- verifies rustfmt and the checked-in schema;
- generates the schema twice and requires identical bytes;
- runs the complete locked crate tests and Clippy with warnings denied;
- executes a separate Rust consumer locked and offline; and
- retains the schema, SHA-256, source status, exact source files, and consumer
  lockfile as bounded evidence.

It uses read-only repository permissions and immutable third-party Action
commits. No registry credentials or public mutation path are present.

## Current-main lifecycle baseline

The former stacked lifecycle source-map repair is already merged and
byte-identical on current `zed-pkg-test/zed-pkg-e2e` main. The current-main
replacement therefore carries only the native-range workflow, this document,
and the external consumer fixture. It does not duplicate `scripts/lifecycle.py`,
its source-map tests, or lifecycle documentation.

The complete repository workflow set remains a promotion gate. The focused
native-range contract must pass alongside the current fixture lifecycle,
install/OCI boundaries, browser E2E, recursive graph and stress, mise runtime,
Git-submodule, and source-map suites before the canary or product layer merges.
