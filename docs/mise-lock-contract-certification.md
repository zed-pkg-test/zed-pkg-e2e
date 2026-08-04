# Complete current `mise.lock` certification

This suite independently certifies the exact current-main `zed-pkg/zed-cli` candidate implementing the complete current project-local `mise.lock` identity contract. The workflow pins a full commit SHA and never follows a mutable branch.

## Immutable candidate

Implementation PR: `zed-pkg/zed-cli#120`

Pinned CLI head:

```text
82ed67aaa98495b5aa85faef1d25671373eb1991
```

Pinned interface head:

```text
c2e049006453c26ca8ca291783f681fce75cb01f
```

## Current mise wire-format canary

The implementation repository includes `tests/fixtures/mise-lock/current-actionlint.lock`, copied from mise commit `72379d0c459808f980a037065ac9c39a60032280`.

That fixture uses mise's current quoted literal platform keys:

```toml
[[tools.actionlint]]
version = "1.7.12"
backend = "aqua:rhysd/actionlint"

[tools.actionlint."platforms.linux-x64"]
checksum = "sha256:..."
url = "https://..."
url_api = "https://api.github.com/..."
provenance = "github-attestations"
```

The cross-platform suite names the current-wire tests explicitly. They require parse, deterministic render back to quoted `"platforms.<target>"` keys, reparse, normalized equality, semantic-digest equality, and provenance-mutation sensitivity. A lock identity that mixes the current quoted form with the earlier nested compatibility form fails closed.

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
- current quoted platform-key wire compatibility;
- deterministic TOML/canonical JSON round trips;
- stable semantic SHA-256 identity;
- credential, fragment, malformed-host, and secret-query URL rejection;
- malformed or missing checksum rejection in frozen-portable mode; and
- explicit rejection of source-only restore state in portable locks.

The highest-risk deterministic canaries run again after the ordinary focused targets so a passing aggregate gate cannot be produced solely by test-filtering mistakes.

## Prior independent build evidence

Before this permanent current-main gate was opened, `zed-pkg-test/zed-pkg-e2e#47`, workflow run `30908244036`, independently executed the complete reviewed DEN-1461 source transformation. Its DEN-1461 job passed and uploaded ordinary-source artifact `den1461-materialized` with digest:

```text
sha256:c07630605d85522850229b03055ea8f6c4439cb7f9c11e66f9d4eada6c651112
```

The current implementation PR reconstructs the same six-file surface directly on current main and preserves the reviewed branch as an explicit commit parent.

## Claim boundary

This suite certifies the complete current manager-lock data and identity contract. It does not claim that `zed env import/verify mise` consumes every represented field, that native installation/activation supports every identity, or that deterministic export and offline replay are complete. Those remain separate DEN-1461 and DEN-1462 gates.

Tracking: DEN-1461, DEN-1481, DEN-1462.

`zed-pkg` is independent of and unrelated to the Zed editor.
