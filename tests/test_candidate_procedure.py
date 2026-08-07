from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import procedure_contract  # noqa: E402

SHA = "a" * 40

VALID_WORKFLOW = f'''name: candidate
on:
  workflow_call:
permissions:
  contents: read
jobs:
  contract:
    steps:
      - run: |
          [[ "$ZED_CLI_REF" =~ ^[0-9a-f]{{40}}$ ]]
          [[ "$HARNESS_REF" =~ ^[0-9a-f]{{40}}$ ]]
          [[ "$ZED_INTERFACES_REF" =~ ^[0-9a-f]{{40}}$ ]]
          python3 - <<'PY'
          re.fullmatch(r"[0-9a-f]{{40}}", ref)
          fixture_refs=refs
          PY
  build:
    steps:
      - uses: actions/checkout@{SHA}
        with:
          ref: ${{{{ env.ZED_CLI_REF }}}}
          persist-credentials: false
      - uses: actions/checkout@{SHA}
        with:
          ref: ${{{{ env.ZED_INTERFACES_REF }}}}
          persist-credentials: false
      - uses: actions/checkout@{SHA}
        with:
          ref: ${{{{ env.HARNESS_REF }}}}
          persist-credentials: false
      - uses: actions/checkout@{SHA}
        with:
          ref: ${{{{ matrix.fixture.ref }}}}
          persist-credentials: false
      - run: |
          python3 scripts/candidate_lifecycle.py --fixture-refs-json "$FIXTURE_REFS"
          sha256sum --check SHA256SUMS
'''

VALID_WRAPPER = '''
if not re.fullmatch(r"[0-9a-f]{40}", ref):
    raise ValueError("root fixture")
raise ValueError("dependency fixture")
command = [
    "fetch",
    "--depth",
    "1",
    "--no-tags",
]
checkout = ["checkout", "--detach", "FETCH_HEAD"]
verify = ["rev-parse", "HEAD"]
'''

VALID_DOCS = '''
# Two gates, two purposes
exact 40-character `zed-cli` commit
exact 40-character `zed-pkg-e2e` harness commit
never follows a fixture default branch
## Full candidate certification
.github/workflows/lifecycle.yml
.github/workflows/e2e.yml
.github/workflows/install-boundaries.yml
same immutable candidate and dependency graph
zed-pkg-test certification runbook
product regression, fixture drift, harness defect, or infrastructure failure
'''


class WorkflowPolicyTests(unittest.TestCase):
    def test_valid_contract_passes(self) -> None:
        procedure_contract.audit_workflow(VALID_WORKFLOW)

    def test_mutable_action_ref_is_rejected(self) -> None:
        with self.assertRaisesRegex(procedure_contract.ContractViolation, "exact commit"):
            procedure_contract.audit_workflow(
                VALID_WORKFLOW.replace(f"actions/checkout@{SHA}", "actions/checkout@v7", 1)
            )

    def test_write_permission_is_rejected(self) -> None:
        with self.assertRaisesRegex(procedure_contract.ContractViolation, "contents: read"):
            procedure_contract.audit_workflow(
                VALID_WORKFLOW.replace("contents: read", "contents: write")
            )

    def test_secret_inheritance_is_rejected(self) -> None:
        with self.assertRaisesRegex(procedure_contract.ContractViolation, "secrets"):
            procedure_contract.audit_workflow(VALID_WORKFLOW + "\nsecrets: inherit\n")

    def test_persisted_checkout_credentials_are_rejected(self) -> None:
        with self.assertRaisesRegex(procedure_contract.ContractViolation, "credentials"):
            procedure_contract.audit_workflow(
                VALID_WORKFLOW.replace("persist-credentials: false", "persist-credentials: true", 1)
            )

    def test_missing_exact_fixture_validation_is_rejected(self) -> None:
        with self.assertRaisesRegex(procedure_contract.ContractViolation, "fullmatch"):
            procedure_contract.audit_workflow(
                VALID_WORKFLOW.replace('re.fullmatch(r"[0-9a-f]{40}", ref)', "pass")
            )


class WrapperAndDocumentationTests(unittest.TestCase):
    def test_valid_wrapper_and_docs_pass(self) -> None:
        procedure_contract.audit_wrapper(VALID_WRAPPER)
        procedure_contract.audit_documentation(VALID_DOCS)

    def test_mutable_clone_boundary_is_rejected(self) -> None:
        with self.assertRaises(procedure_contract.ContractViolation):
            procedure_contract.audit_wrapper(VALID_WRAPPER + '\nref: main\n')

    def test_missing_failure_classification_is_rejected(self) -> None:
        with self.assertRaises(procedure_contract.ContractViolation):
            procedure_contract.audit_documentation(
                VALID_DOCS.replace(
                    "product regression, fixture drift, harness defect, or infrastructure failure",
                    "failure",
                )
            )


if __name__ == "__main__":
    unittest.main()
