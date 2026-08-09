# Canonical paired test-fleet index

The live test-fleet manifest is stored as deterministic compressed/base64 parts under `bootstrap/test-org-fleet.json.gz.b64.parts/`.

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

It fails unless the live manifest still describes:

- 19 production/test pairs;
- 22 retained repositories;
- 319 specialized repositories;
- 19 governance repositories;
- 338 managed repositories;
- 360 total expected repositories; and
- explicit exclusion of `r2g` and `r2g-test`.

The fleet contract workflow publishes the full JSON index to its GitHub Actions job summary, making manifest changes visible in review before provisioning occurs.
