# Candidate validation through `zed-pkg-test`

`github.com/zed-pkg-test` is the disposable compatibility laboratory for changes
to the independent `zed-pkg` package manager. Production repositories remain in
`github.com/zed-pkg`; fixtures and cross-repository certification remain here.

## Two gates, two purposes

### Pull-request candidate smoke

`.github/workflows/candidate-smoke.yml` is a reusable, least-privilege workflow
for an unmerged `zed-pkg/zed-cli` commit. A caller supplies:

- the exact 40-character `zed-cli` commit;
- the exact 40-character `zed-pkg-e2e` harness commit; and
- a JSON matrix of fixture repositories pinned to exact commits.

The workflow builds the candidate once, checks its checksum in every matrix
job, and runs the existing stateless lifecycle contract against representative
libraries, applications, polyglot targets, workspaces, multi-version packages,
awkward layouts, submodules, subtrees, and a fail-closed non-package repository.

`scripts/candidate_lifecycle.py` extends the normal lifecycle harness only at
the dependency-fetch boundary. Every fixture dependency must appear in the
supplied repository-to-commit map. Missing pins fail closed; the candidate
smoke never follows a fixture default branch.

This gate is intended for rapid regression detection on every relevant CLI
pull request. It is evidence, but it is **not** release certification.

### Full candidate certification

Before merging a change that affects resolution, manifests, lockfiles,
packing, publishing, installation, registry behavior, package pages,
authentication, Docker/OCI boundaries, or cross-language fan-out, open a
dedicated PR in this repository that pins the same candidate SHA in:

- `.github/workflows/lifecycle.yml`;
- `.github/workflows/e2e.yml`; and
- `.github/workflows/install-boundaries.yml`.

A candidate is certified only when all applicable workflows pass against the
same immutable candidate and dependency graph. The canonical evidence and
failure-classification rules live in the Linear document
**zed-pkg-test certification runbook**, attached to the
`github.com/zed-pkg` project.

## Reusable caller

A production repository should pin the reusable workflow itself by commit:

```yaml
jobs:
  zed-pkg-test:
    uses: zed-pkg-test/zed-pkg-e2e/.github/workflows/candidate-smoke.yml@<exact-harness-sha>
    with:
      zed_cli_ref: ${{ github.event.pull_request.head.sha || github.sha }}
      harness_ref: <exact-harness-sha>
```

Do not call a mutable branch or tag. The workflow receives no secrets and
declares only read access to repository contents, so pull-request code cannot
inherit publication credentials.

## Today’s candidate

The initial rollout exercises `zed-pkg/zed-cli#36` at
`8d318ce457841c485e394575dee17fbf78fcc63c`, with
`zed-pkg/zed-interfaces` pinned to
`dc0e0a0620b9462817950b552d3d334a184b1cb1`.

Record the smoke workflow run and any later lifecycle, browser, and
install-boundary runs on the owning Linear issue. A smoke failure must be
classified as a product regression, fixture drift, harness defect, or
infrastructure/transient failure before code or assertions are changed.
