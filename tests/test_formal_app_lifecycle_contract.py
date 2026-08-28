from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from verify_formal_app_lifecycle_contract import (  # noqa: E402
    ContractError,
    EXPECTED_EVENTS,
    EXPECTED_OUTCOMES,
    EXPECTED_PHASES,
    EXPECTED_WITNESSES,
    verify,
)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class FormalAppLifecycleContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        witness_lines = "\n".join(f'  "{value}",' for value in sorted(EXPECTED_WITNESSES))
        write(
            self.root / "formal/app-lifecycle.fm.toml",
            'schema_version = 1\nproject = "zed-sync"\nmodel = "app-lifecycle-v1"\n'
            'language = "quint"\nspec = "formal/app_lifecycle.qnt"\n'
            'main = "app_lifecycle"\ninvariants = ["app_lifecycle_safety"]\n'
            f"witnesses = [\n{witness_lines}\n]\n\n"
            '[toolchain]\nquint = "0.32.0"\n\n'
            '[verification]\nbackend = "tlc"\nexhaustive_finite_model = true\n',
        )
        symbols = "\n".join(sorted({"app_lifecycle_safety", *EXPECTED_WITNESSES}))
        write(self.root / "formal/app_lifecycle.qnt", symbols)

        events = sorted(EXPECTED_EVENTS)
        outcomes = sorted(EXPECTED_OUTCOMES)
        phases = sorted(EXPECTED_PHASES)
        steps = []
        for index, event in enumerate(events):
            steps.append({
                "event": {"type": event},
                "outcome": outcomes[index % len(outcomes)],
                "state": {
                    "phase": phases[index % len(phases)],
                    "operation": "none",
                    "generation": index,
                    "active_token": None,
                    "desired_running": False,
                    "online": False,
                    "failure": None,
                },
            })
        cases = [
            {"name": f"case-{index}", "steps": steps[index:index + 2]}
            for index in range(5)
        ]
        cases.append({"name": "case-5", "steps": steps[5:]})
        write(
            self.root / "protocol/formal-app-lifecycle.json",
            json.dumps({"schema_version": 1, "model": "app-lifecycle-v1", "cases": cases}),
        )
        write(
            self.root / "protocol/formal-app-lifecycle.schema.json",
            json.dumps({
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$defs": {
                    "event": {"properties": {"type": {"enum": sorted(EXPECTED_EVENTS)}}},
                    "step": {"properties": {"outcome": {"enum": sorted(EXPECTED_OUTCOMES)}}},
                    "state": {"properties": {"phase": {"enum": sorted(EXPECTED_PHASES)}}},
                },
            }),
        )
        for implementation, refinement in (
            ("src/lifecycle.rs", "tests/formal_app_lifecycle.rs"),
            ("sdk/src/lifecycle.mjs", "sdk/test/formal-app-lifecycle.test.mjs"),
            ("dart/zed_sync/lib/src/lifecycle.dart", "dart/zed_sync/test/formal_app_lifecycle_test.dart"),
        ):
            write(self.root / implementation, "lifecycle reducer\n")
            write(self.root / refinement, "formal-app-lifecycle.json\n")

    def test_complete_cross_runtime_contract_passes(self) -> None:
        verify(self.root)

    def test_missing_runtime_refinement_fails_closed(self) -> None:
        (self.root / "dart/zed_sync/test/formal_app_lifecycle_test.dart").unlink()
        with self.assertRaisesRegex(ContractError, "missing Dart lifecycle refinement"):
            verify(self.root)

    def test_incomplete_outcome_coverage_fails_closed(self) -> None:
        path = self.root / "protocol/formal-app-lifecycle.json"
        fixture = json.loads(path.read_text(encoding="utf-8"))
        for case in fixture["cases"]:
            for step in case["steps"]:
                if step["outcome"] == "stale":
                    step["outcome"] = "applied"
        path.write_text(json.dumps(fixture), encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "outcome coverage drifted"):
            verify(self.root)


if __name__ == "__main__":
    unittest.main()
