# Federated `zed-pkg-test` scenario acceptance

This harness turns four independent repositories in the `zed-pkg-test` organization into one executable acceptance layer for a checksum-verified `zed` release-candidate binary.

## Covered repositories

| Test repository | Executable acceptance surface |
| --- | --- |
| `zed-pkg-test/offline-cache-e2e` | cold install, offline frozen replay, shared cache reuse, corrupt-cache rejection, and immutable-registry recovery |
| `zed-pkg-test/version-solver-e2e` | compatible-range selection, exact pins, deterministic conflict diagnostics, and stable SemVer build-metadata outcomes |
| `zed-pkg-test/security-adversarial-e2e` | symlink containment, target traversal rejection, explicit build-hook consent, and tampered-lock fail-closed behavior |
| `zed-pkg-test/manager-interop-e2e` | parser-only/read-only mise and asdf verification, ambiguity rejection, moving-selector rejection, and typed unsupported-manager boundaries |

The scenario runner creates isolated registries, homes, consumers, and package graphs under a disposable root. It does not mutate any of the four source repositories.

## Binary provenance

The workflow queries the public `zed-pkg/zed-cli` Actions artifact inventory for the newest non-expired `zed-x86_64-unknown-linux-gnu` artifact produced from a `release/**` branch. Before execution it verifies:

1. the downloaded Actions artifact ZIP against GitHub's recorded `sha256:` digest;
2. the inner `zed-x86_64-unknown-linux-gnu.tar.gz` against the checksum packaged by the release workflow;
3. the source commit recorded on the selected workflow artifact.

The JSON report records the CLI version, source commit, inner archive SHA-256, suite inventory, and every individual check.

## August 6, 2026 evidence

The first execution used:

- CLI: `zed 0.1.0`
- source commit: `2f98df7b0f3f20bd8eaec6abbe768566833589bc`
- inner archive SHA-256: `ed55e39811cac6303162536397562ed004c96c4cd7e09b531f0d323f59142aff`
- result: **4 suites and 19 checks passed**

The machine-readable receipt is retained at `evidence/2026-08-06-zed-0.1.0-rc3-test-org.json`.

## Running locally

```bash
ZED_BIN=/absolute/path/to/zed \
ZED_BINARY_ARCHIVE=/absolute/path/to/zed-x86_64-unknown-linux-gnu.tar.gz \
ZED_SOURCE_COMMIT=<40-character-commit> \
ZED_E2E_ROOT=/tmp/zed-test-org-federation \
ZED_E2E_REPORT=/tmp/federated-test-org-report.json \
python3 scripts/run-federated-test-org-scenarios.py
```

The runner exits nonzero on the first failed invariant and still prints the partial JSON report to stderr.

## Scheduled execution

`.github/workflows/federated-test-org-scenarios.yml` runs daily in the test organization and supports manual dispatch. The workflow has read-only repository and Actions permissions. Cross-repository artifact access uses `TEST_FLEET_READ_TOKEN` when configured and otherwise attempts the ephemeral GitHub token.

A passing workflow must report exactly these four suites and exactly 19 passing checks. Adding or removing checks therefore requires an intentional harness update rather than silently changing the acceptance surface.
