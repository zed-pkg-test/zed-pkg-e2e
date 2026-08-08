# External Nix bundle-write canary

Tracking: DEN-1592

This repository independently certifies the merged product command:

```bash
zed interop nix bundle write \
  --frozen \
  --flake-lock ./approved-flake.lock \
  --out ./exports/package-flake
```

The workflow pins `zed-pkg/zed-cli@1b282b94efc994bb9352b572c9652d5a078787f2`.
The canaries import no Rust implementation module: they build that exact release
binary and drive only the executable plus Nix.

## Synthetic full-command contract

On Ubuntu 24.04 and macOS 15, `scripts/nix_bundle_write_canary.py` proves:

- invocation below the project root discovers `.zpkg.toml` and `.zpkg.lock`;
- a missing output is created as a deterministic standalone flake;
- the compact receipt is `zed.nix-flake-bundle-write/v1` and reports the
  canonical published destination;
- source project bytes remain unchanged and the configured Zed home is never
  created;
- an exact second invocation returns `already-current` without rewriting bytes;
- the visible `--output` alias and environment-only flag contract work;
- an unknown option fails before output creation;
- a caller-supplied symlink as the immediate output parent is rejected without
  writing through its target;
- tampered caller-owned output fails unchanged;
- generated text does not leak the source path, configured Zed home, or invalid
  registry sentinel; and
- after one explicit locked acquisition step, the generated flake passes
  `nix flake check --offline` and reproduces the same `nix build --offline`
  store output.

## Immutable real-fixture replay

`scripts/nix_bundle_write_fixture_canary.py` independently checks out
`zed-pkg-test/node-lib@222cdf57f48530fce8e6c1f58632d9676203512e`,
creates two clean copies below unrelated absolute paths, adds the same explicit
artifact-only Nix intent and empty frozen lock, and requires the command to emit
byte-identical standalone bundles.

The real-fixture canary independently verifies:

- public package identity `zed-pkg-test/node-lib@1.0.0`;
- canonical `node_lib` Nix attribute and current runner system;
- exact artifact SHA-256 and size agreement with the retained plan;
- sorted, unique inventory entries whose bytes and sizes are independently
  rehashed;
- immutable Nixpkgs revision and NAR hash evidence;
- identical domain-separated bundle identity across absolute project paths;
- no symlinks in either output;
- no source, temporary, home, credential, or invalid-registry value in generated
  text;
- unchanged disposable project copies;
- a clean immutable fixture checkout; and
- non-creation of the configured Zed home.

## Reproducibility and trust

`.zed-nix-bundle-write-cli-ref` contains the default full merged commit pin.
Manual workflow overrides must also be full 40-character commits. The shared
`zed-interfaces` revision is asserted in both `Cargo.toml` and `Cargo.lock`.
The real fixture and every third-party Action are also full-commit pins.

The workflow has read-only repository permissions, disables persisted checkout
credentials, owns all package, Nix, output, and Zed state below fresh
runner-temporary directories, and uploads bounded diagnostics only on failure.
It never publishes to a Zed registry, Cachix, Attic, GitHub release, package
namespace, or OCI registry.

The canaries complement product-repository unit and integration tests. They are
persistent post-merge drift gates for the actual merged binary and immutable
external package inputs.
