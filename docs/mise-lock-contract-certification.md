# Complete current `mise.lock` certification

This suite independently checks the exact `zed-pkg/zed-cli` candidate that
implements the complete current project-local `mise.lock` identity contract.
The workflow pins a full commit SHA and never follows a mutable branch.

## Certified behavior

Across Ubuntu 24.04, macOS 15, and Windows Server 2025, the suite requires:

- compilation of the complete lock model;
- strict formatting and all-target Clippy with warnings denied;
- unit and public consumer tests;
- option-dependent and multi-version tool identities;
- compact and detailed platform representations;
- conda/pkgx shared-package references;
- ordered additional release artifacts;
- provenance and verification-state relationships;
- deterministic TOML/canonical JSON round trips;
- stable semantic SHA-256 identity;
- credential, fragment, and secret-query URL rejection;
- malformed/missing checksum rejection in frozen-portable mode; and
- explicit rejection of source-only restore state in portable locks.

The deterministic canaries run again after the ordinary focused targets so a
passing aggregate gate cannot be produced solely by test filtering mistakes.

## Claim boundary

This suite certifies the manager-lock contract, not native installation or
shell activation. It does not claim that `zed env import mise` consumes every
represented field yet. Integration into import/verify, native `EnvironmentLock`
translation, deterministic export, and clean-room offline replay remain
separate gates.

Implementation: `zed-pkg/zed-cli#109`

Tracking: DEN-1461, DEN-1481, DEN-1462.
