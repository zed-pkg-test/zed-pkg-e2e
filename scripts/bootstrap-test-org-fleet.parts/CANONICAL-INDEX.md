# Canonical fleet index

The live manifest is exported by `scripts/export-test-org-fleet-index.mjs`.

This file lives beside the split generator parts so changes to the deterministic index trigger the existing fleet contract workflow. The exporter is the supported source for per-organization expected names and counts; archived portfolio exports are not authoritative.

Current enforced totals are 18 pairs, 22 retained repositories, 301 specialized repositories, 18 governance repositories, 319 managed repositories, and 341 total expected repositories. `r2g` and `r2g-test` remain excluded.
