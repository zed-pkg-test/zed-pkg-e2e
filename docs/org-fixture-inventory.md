# Organization fixture inventory

`zed-pkg-test` is an executable compatibility surface for `zed-pkg`. A fixture
repository is not considered covered merely because it exists: it must be
classified, included in the full lifecycle matrix, and probeable as a package.

## Checked contracts

`fixtures/org-repositories.json` is the reviewed inventory. The inventory gate
fails when:

- a live public repository is missing from the inventory or the lifecycle matrix;
- a classified repository disappears, is archived, disabled, forked, private, or
  changes its default branch away from `main`;
- a package fixture loses its root `.zpkg.toml`, emits invalid TOML, or lacks the
  required `[package]` identity fields;
- `scripts/lifecycle.py` classifies a package source outside the reviewed
  inventory or changes the non-package boundary.

The one manifestless repository is `zed-pkg-e2e`, the orchestrator itself.

## Local commands

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 scripts/org_fixture_inventory.py
```

The live probe uses read-only GitHub API access and needs no personal access
token:

```bash
GITHUB_TOKEN=... python3 scripts/org_fixture_inventory.py \
  --live \
  --output /tmp/zed-pkg-test-inventory.json
```

GitHub Actions supplies its ephemeral read-only repository token. The script
also works without a token against the public API, subject to anonymous rate
limits.

## Adding a fixture

Add the repository to `fixtures/org-repositories.json`, add it to the
`strategy.matrix.repo` list in `.github/workflows/lifecycle.yml`, and make sure
the root manifest is valid. The unit tests and live E2E gate intentionally fail
until all three surfaces agree.
