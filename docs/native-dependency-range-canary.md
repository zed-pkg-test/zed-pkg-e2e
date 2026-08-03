# Native dependency range and exact-lock canary

This canary certifies the public `zed-interfaces` contract that translates npm
and Cargo dependency declarations before freezing one exact artifact identity.
It does not invoke npm, Cargo, crates.io, the npm registry, Nix, or another
resolver.

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

## Frozen lock boundary

A successful resolution records the original declaration, deterministic
canonical requirement, exact strict-SemVer package version, lowercase nonzero
artifact SHA-256, size, and archive format. The public validator recomputes the
translation and rejects canonical-requirement drift, a resolved version outside
the range, malformed artifact identity, duplicate candidates, and SemVer build
metadata.

## Unsupported syntax

The v1 canary requires fail-closed rejection for unions, hyphen ranges, npm
dist-tags and aliases, workspace/local/Git/URL sources, npm comma syntax, Cargo
whitespace-only comparator intersections, and npm-style Cargo `x` wildcards.
No rejected syntax may silently degrade to an opaque exact string.

## Reproducibility and isolation

The workflow pins one exact 40-character `zed-interfaces` commit, checks it out
without persisted credentials, formats and tests the complete crate, generates
the JSON Schema, runs Clippy with warnings denied, and executes a separate Rust
consumer offline. The generated schema, formatted source, consumer lockfile,
source status, and checksum are retained as evidence.

After the formatted product and checked-in schema are committed, the workflow is
ratcheted to formatter and schema-drift check mode. The immutable interface pin
must then be advanced to the final reviewed product commit before merge.
