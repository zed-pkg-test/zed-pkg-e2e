# Current-main native lockfile provenance canary

This canary certifies the exact additive `[[native-dependency]]` contract in
`zed-pkg/zed-interfaces@a23cdd2ac509a39ca6fd6d21a3774fdd3a0f7660`.

It runs from current E2E `main` and adds only this document, one read-only
workflow, and a two-file external Rust consumer. It does not duplicate the
range, native-registry, lifecycle, browser, install, graph, stress, source-map,
or environment canaries already on `main`.

The contract preserves legacy lockfile v1 parsing while freezing source-aware
npm/Cargo resolutions as exact versions with immutable artifact SHA-256, size,
and format. It validates entries during parse, write, and upsert; rejects
canonical-requirement drift, duplicate `(registry, package.name)` identities,
malformed artifact provenance, and unsatisfied exact locks; and writes package,
native dependency, and Nix provenance in deterministic order.

The focused workflow checks the exact candidate with formatting, deterministic
schema regeneration, every locked test target, strict Clippy, an external
consumer executed locked and offline, and current pinned CLI/API/web consumers.
No registry credentials, publication route, moving product pin, or persisted
checkout credentials are present.
