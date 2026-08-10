# Live paired test-fleet audit

The exact repository denominator is exported from the current compressed manifest by:

```sh
node scripts/export-test-org-fleet-index.mjs > canonical-test-fleet-index.json
```

The live auditor intentionally does **not** hard-code a pair count or repository count. It reads the same manifest and compares each production/test pair with GitHub's current organization inventories. The workflow then requires the auditor's pair and expected-repository totals to match the canonical exporter for the exact commit being tested.

This matters because the fleet evolves. At the time this document was updated, current `main` exported 19 production/test pairs and 360 expected repository identities; future reviewed manifest changes may legitimately change those totals without requiring a second source-of-truth edit in the auditor.

For every pair the auditor reports:

- canonical repositories present and missing;
- independently added repositories, which are preserved as extras;
- archived, disabled, or default-branch-less canonical repositories;
- production and test organization repository counts; and
- production-side hygiene findings as warnings.

`r2g` and `r2g-test` remain excluded by the canonical manifest and exporter.

## Visibility boundary

An exact audit requires metadata visibility into private repositories across every paired test organization. The repository-scoped `GITHUB_TOKEN` cannot provide that visibility and is deliberately rejected for the live job because permission gaps would otherwise become false missing-repository findings.

Scheduled and manually dispatched live audits require `FLEET_AUDIT_TOKEN`, which must be either:

- a read-only cross-organization token limited to repository metadata; or
- an approved short-lived GitHub App installation token supplied under the same secret name.

The workflow refuses to substitute the normal `GITHUB_TOKEN` and fails with an explicit configuration message when the cross-organization credential is absent. Pull-request and push jobs remain secret-free and run only syntax, unit, and canonical-exporter contracts.

## Local use

```sh
GH_TOKEN="$(gh auth token)" node scripts/audit-live-test-org-fleet.mjs
GH_TOKEN="$(gh auth token)" node scripts/audit-live-test-org-fleet.mjs --json > live-audit.json
GH_TOKEN="$(gh auth token)" node scripts/audit-live-test-org-fleet.mjs --org shared-auth-test
```

The token needs repository metadata read access only. The auditor never writes GitHub state or prints the token.

## Failure semantics

The live job fails independently when:

- the auditor cannot run or produce valid JSON;
- its pair count differs from the canonical exporter;
- its expected repository count differs from the canonical exporter;
- a canonical repository is missing;
- a canonical repository is archived, disabled, or lacks a default branch; or
- cross-organization credentials are absent.

Extras are reported but preserved. Use `--strict-extras` only for an intentional exact-equality investigation; later product-specific repositories are not deletion targets.

## Automation and evidence

`.github/workflows/live-test-org-fleet-audit.yml` runs the offline auditor and canonical-exporter contracts on relevant pull requests and manifest changes. The exact cross-organization comparison runs only on the daily schedule or manual dispatch, where `FLEET_AUDIT_TOKEN` is required.

The live job writes the pair-by-pair table, exact missing repository names, and canonical hygiene findings to the GitHub step summary. It retains the exact canonical index plus sanitized JSON and stderr evidence, making portfolio drift visible without relying on a stale hand-maintained count table or confusing permission gaps with repository deletion.
