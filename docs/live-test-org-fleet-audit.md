# Live paired test-fleet audit

The canonical manifest describes 18 production/test organization pairs, 301 specialized repositories, 18 organization governance repositories, and 22 retained `zed-pkg-test` fixtures. Together these form 341 managed repository identities. `r2g` and `r2g-test` remain excluded.

`node scripts/audit-live-test-org-fleet.mjs` compares the manifest with GitHub's current organization inventories. For each pair it reports:

- canonical repositories present and missing;
- independently added repositories, which are preserved as extras;
- archived, disabled, or default-branch-less canonical repositories;
- production and test organization repository counts; and
- production-side hygiene findings as warnings.

The default exit code is non-zero for missing canonical repositories or canonical test repositories with a hygiene failure. Extra repositories are not failures because later product-specific work must not be deleted or overwritten. Use `--strict-extras` only for an intentional exact-equality check.

## Local use

```sh
GH_TOKEN="$(gh auth token)" node scripts/audit-live-test-org-fleet.mjs
GH_TOKEN="$(gh auth token)" node scripts/audit-live-test-org-fleet.mjs --json > live-audit.json
GH_TOKEN="$(gh auth token)" node scripts/audit-live-test-org-fleet.mjs --org shared-auth-test
```

The token needs only repository metadata read access. The auditor never writes GitHub state and never prints the token.

## Automation

`.github/workflows/live-test-org-fleet-audit.yml` runs unit contracts and the full live comparison on relevant pull requests, manifest changes on `main`, a daily schedule, and manual dispatch. It uses read-only permissions, a commit-pinned checkout action, and GitHub's ephemeral workflow token.

The workflow writes the pair-by-pair table and exact missing names to the GitHub step summary. This makes portfolio drift visible without relying on a stale hand-maintained count table.
