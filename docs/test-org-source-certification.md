# Generated test-org source certification

The paired `*-test` repositories have three different evidence meanings. They must never be reported as equivalent.

## Credential-free contract lane

Every generated repository has a pull-request workflow that validates the committed test plan, immutable source pins, declared dependency lanes, and any product-specific tests added outside the generated file set.

This lane deliberately receives no cross-organization credential. It can prove that the committed snapshot is internally coherent, but it cannot prove that a private source repository still matches the snapshot or compiles at the pinned commit.

Product-specific overlays are preserved. The fleet generator owns only its explicit managed paths, including `README.md`, `test-plan.json`, `source-pins.json`, generated workflows, dependency metadata, and source gates. Files such as executable fixtures, browser suites, contract parsers, and product-specific workflows remain repository-owned. A fleet rerun must update managed files without deleting or replacing those overlays.

## Generic protected source-access lane

The generic generated integration workflow runs only when the organization variable `ENABLE_TEST_FLEET_INTEGRATION` is exactly `true` and the protected read credential is configured.

The credential is passed only to the pinned checkout action. Generated workflows must not fall back to `github.token`, because the repository token does not establish the intended cross-organization read authority and can turn a missing credential into a misleading partial run. Persisted checkout credentials remain disabled.

The generic generated lane validates protected source access and the generated plan. It does **not** know how to build or test every product and therefore never certifies a source pin by itself. A successful generic lane reports `sourceAccessPassed: true` and `certified: false`, with a reason stating that product certification is still required.

A skipped lane is **not** source certification.

## Product-specific executable certification

A repository may preserve a product-specific executable overlay that checks out the exact reviewed source, verifies its commit identity, and executes the relevant build, lint, unit, integration, browser, restart, protocol, or security tests.

Only that executable product lane may report `certified: true`, and only after all required checks pass. A plan-only run, credential check, source checkout, static snapshot comparison, or skipped job is insufficient.

## Durable status artifact

Every generic integration workflow includes a separate status job with `if: always()`. It records:

- whether the protected lane was enabled;
- whether it executed;
- whether source access and the generated plan passed;
- `certified: false` because the generic lane is not product-aware;
- the raw dependency-job result;
- a bounded reason string.

Generic expected meanings:

| Integration result | `executed` | `sourceAccessPassed` | `certified` | Meaning |
|---|---:|---:|---:|---|
| `success` | true | true | false | Protected source access passed; product certification remains required. |
| `failure` / `cancelled` | true | false | false | Protected access or plan validation did not pass. |
| `skipped` | false | false | false | The protected lane was not enabled. |

A product-specific certification overlay may publish a separate status artifact whose `certified` field becomes true only after its exact source tests pass.

Status artifacts contain no source credential and are safe to retain as metadata-only evidence.

## Update protocol

1. Reconcile source changes in the owning repository first.
2. Record an immutable source commit in the generated plan.
3. Preserve all product-specific overlay files.
4. Run the credential-free contract lane on the harness pull request.
5. Run the protected source-access lane when cross-organization access must be proven.
6. Run the product-specific executable lane before claiming source compilation or integration certification.
7. Link the status artifacts and product evidence from the source pull request and Linear issue.
8. When conflicts occur, preserve runtime behavior, documentation, client compatibility, and security policy as separate meanings. Never choose an entire conflict side solely because it is newer.

## Security boundary

Credentials belong only in protected organization or repository secrets. Do not place a PAT, GitHub App private key, cloud credential, or source token in generated plans, repository files, workflow inputs, command-line arguments, summaries, artifacts, issues, or Linear documents.
