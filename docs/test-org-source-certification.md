# Generated test-org source certification

The paired `*-test` repositories have two different evidence lanes. They must never be reported as equivalent.

## Credential-free contract lane

Every generated repository has a pull-request workflow that validates the committed test plan, immutable source pins, declared dependency lanes, and any product-specific tests added outside the generated file set.

This lane deliberately receives no cross-organization credential. It can prove that the committed snapshot is internally coherent, but it cannot prove that a private source repository still matches the snapshot or compiles at the pinned commit.

Product-specific overlays are preserved. The fleet generator owns only its explicit managed paths, including `README.md`, `test-plan.json`, `source-pins.json`, generated workflows, dependency metadata, and source gates. Files such as executable fixtures, browser suites, contract parsers, and product-specific workflows remain repository-owned. A fleet rerun must update managed files without deleting or replacing those overlays.

## Protected source-integration lane

The generated integration workflow runs only when the organization variable `ENABLE_TEST_FLEET_INTEGRATION` is exactly `true` and the protected secret `TEST_FLEET_READ_TOKEN` is configured.

The token is passed only to the pinned checkout action. Generated workflows must not fall back to `github.token`, because the repository token does not establish the intended cross-organization read authority and can turn a missing credential into a misleading partial run. Persisted checkout credentials remain disabled.

A successful protected lane may certify the exact source pin only after its product-specific executable tests pass. A skipped lane is **not** source certification.

## Durable status artifact

Every generated integration workflow includes a separate `certification-status` job with `if: always()`. It records:

- whether the protected lane was enabled;
- whether it executed;
- whether it passed and therefore certified the source pin;
- the raw dependency-job result;
- a bounded reason string.

Expected meanings:

| Integration result | `executed` | `certified` | Meaning |
|---|---:|---:|---|
| `success` | true | true | Protected source integration passed. |
| `failure` / `cancelled` | true | false | Integration executed but did not certify the source. |
| `skipped` | false | false | Variable or protected credential lane was not enabled. |

The JSON status artifact contains no source credential and is safe to retain as test evidence.

## Update protocol

1. Reconcile source changes in the owning repository first.
2. Record an immutable source commit in the generated plan.
3. Preserve all product-specific overlay files.
4. Run the credential-free contract lane on the harness pull request.
5. Enable and run the protected source lane before claiming source compilation or integration certification.
6. Link the status artifact and product evidence from the source pull request and Linear issue.
7. When conflicts occur, preserve runtime behavior, documentation, client compatibility, and security policy as separate meanings. Never choose an entire conflict side solely because it is newer.

## Security boundary

Credentials belong only in protected organization or repository secrets. Do not place a PAT, GitHub App private key, cloud credential, or source token in generated plans, repository files, workflow inputs, command-line arguments, summaries, artifacts, issues, or Linear documents.
