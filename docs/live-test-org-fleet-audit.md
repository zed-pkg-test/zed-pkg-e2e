# Live paired test-fleet audit

The canonical manifest describes 18 production/test organization pairs, 301 specialized repositories, 18 organization governance repositories, and 22 retained `zed-pkg-test` fixtures. Together these form 341 managed repository identities. `r2g` and `r2g-test` remain excluded.

`node scripts/audit-live-test-org-fleet.mjs` compares the manifest with GitHub's current organization inventories. For each pair it reports:

- canonical repositories present and missing;
- independently added repositories, which are preserved as extras;
- archived, disabled, or default-branch-less canonical repositories;
- production and test organization repository counts; and
- production-side hygiene findings as warnings.

The default exit code is non-zero for missing canonical repositories or canonical test repositories with a hygiene failure. Extra repositories are not failures because later product-specific work must not be deleted or overwritten. Use `--strict-extras` only for an intentional exact-equality check.

## Visibility requirement

An exact audit requires metadata visibility into private repositories across all 18 test organizations. The repository-scoped `GITHUB_TOKEN` issued to `zed-pkg-test/zed-pkg-e2e` cannot provide that visibility: it can enumerate public sibling repositories but not private repositories owned by the other organizations. Treating that token as authoritative produces false missing-repository findings.

For autonomous scheduled or manually dispatched audits, configure a read-only `FLEET_AUDIT_TOKEN` repository secret. It may be either:

- a fine-grained token limited to repository metadata read access for the paired organizations; or
- a short-lived installation token produced by an organization-approved GitHub App and passed to the workflow under the same secret name.

The workflow refuses to substitute the normal `GITHUB_TOKEN` and fails with an explicit configuration message when the cross-organization credential is absent. Pull-request and push jobs still run the complete offline unit contract without secrets.

## Local use

```sh
GH_TOKEN="$(gh auth token)" node scripts/audit-live-test-org-fleet.mjs
GH_TOKEN="$(gh auth token)" node scripts/audit-live-test-org-fleet.mjs --json > live-audit.json
GH_TOKEN="$(gh auth token)" node scripts/audit-live-test-org-fleet.mjs --org shared-auth-test
```

The token needs only repository metadata read access. The auditor never writes GitHub state and never prints the token.

## Automation

`.github/workflows/live-test-org-fleet-audit.yml` runs offline unit contracts on relevant pull requests and manifest changes. The exact cross-organization comparison runs only on the daily schedule or manual dispatch, where `FLEET_AUDIT_TOKEN` is required.

The live job writes the pair-by-pair table and exact missing names to the GitHub step summary and retains sanitized JSON/stderr evidence. This makes portfolio drift visible without relying on a stale hand-maintained count table or confusing permission gaps with repository deletion.
