# Canonical paired test-fleet index

The live test-fleet manifest is composed from deterministic compressed/base64 parts under `bootstrap/test-org-fleet.json.gz.b64.parts/` plus the audited additive overlay at `bootstrap/test-org-fleet.extensions.json`.

Use the deterministic exporter instead of copied spreadsheets, archived reports, or manually maintained counts:

```bash
node scripts/export-test-org-fleet-index.mjs > /tmp/canonical-test-fleet-index.json
```

The exporter includes, for every paired test organization:

- retained baseline repositories;
- the public `.github` governance repository;
- every specialized managed repository;
- managed and total expected counts; and
- the exact expected repository-name set.

It fails unless the composed live manifest still describes:

- 19 production/test pairs;
- 22 retained repositories;
- 322 specialized repositories;
- 19 governance repositories;
- 341 managed repositories;
- 363 total expected repositories; and
- explicit exclusion of `r2g` and `r2g-test`.

The fleet contract workflow publishes the full JSON index to its GitHub Actions job summary, making manifest changes visible in review before provisioning occurs.
