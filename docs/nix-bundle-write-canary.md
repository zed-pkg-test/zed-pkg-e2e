# External Nix bundle-write canary

Tracking: DEN-1592

This repository independently certifies the product command:

```bash
zed interop nix bundle write \
  --frozen \
  --flake-lock ./approved-flake.lock \
  --out ./exports/package-flake
```

The canary imports no Rust implementation module. It checks out one immutable
`zed-pkg/zed-cli` commit, builds the release binary, constructs a disposable
package project, and drives only that executable plus Nix.

## Certified boundaries

On Ubuntu 24.04 and macOS 15, the harness proves:

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

## Reproducibility and trust

`.zed-nix-bundle-write-cli-ref` contains the default full commit pin. Manual
workflow overrides must also be full 40-character commits. The shared
`zed-interfaces` revision is asserted in both `Cargo.toml` and `Cargo.lock`.

The workflow has read-only repository permissions, uses full-SHA Action pins,
disables persisted checkout credentials, owns all package/Nix/Zed state below a
fresh runner-temporary directory, and uploads bounded diagnostics only on
failure. It never publishes to a Zed registry, Cachix, Attic, GitHub release, or
OCI registry.

The canary complements product-repository unit and integration tests. It does
not replace them: a candidate is mergeable only after its exact product checks
and this independent binary-level contract agree.
