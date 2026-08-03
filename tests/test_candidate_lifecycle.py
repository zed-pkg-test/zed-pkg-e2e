from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import candidate_lifecycle  # noqa: E402

SHA_A = "a" * 40
SHA_B = "b" * 40


class FixtureRefParsingTests(unittest.TestCase):
    def test_accepts_exact_lowercase_commits(self) -> None:
        self.assertEqual(
            candidate_lifecycle.parse_fixture_refs(
                f'{{"node-app":"{SHA_A}","node-lib":"{SHA_B}"}}'
            ),
            {"node-app": SHA_A, "node-lib": SHA_B},
        )

    def test_rejects_non_object_empty_and_mutable_inputs(self) -> None:
        invalid = (
            "[]",
            "{}",
            "not-json",
            '{"node-app":"main"}',
            '{"node-app":"abc123"}',
            f'{{"Node-App":"{SHA_A}"}}',
            f'{{"../node-app":"{SHA_A}"}}',
            f'{{"node-app":"{SHA_A.upper()}"}}',
        )
        for raw in invalid:
            with self.subTest(raw=raw):
                with self.assertRaises(argparse.ArgumentTypeError):
                    candidate_lifecycle.parse_fixture_refs(raw)

    def test_rejects_non_string_values(self) -> None:
        with self.assertRaises(argparse.ArgumentTypeError):
            candidate_lifecycle.parse_fixture_refs('{"node-app":42}')


class PinnedHarnessTests(unittest.TestCase):
    def _harness(self, refs: dict[str, str]):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        harness = candidate_lifecycle.PinnedHarness.__new__(
            candidate_lifecycle.PinnedHarness
        )
        harness.repo = "node-app"
        harness.fixture = Path(temp.name) / "root"
        harness.fixture_refs = refs
        harness.clones = {}
        harness.dependency_repos = Path(temp.name) / "dependencies"
        harness.assert_git_clean = lambda *_args, **_kwargs: None
        commands: list[list[str]] = []

        def run(command, **_kwargs):
            argv = [str(value) for value in command]
            commands.append(argv)
            if "rev-parse" in argv:
                return refs.get("node-lib", SHA_B) + "\n"
            return ""

        harness.run = run
        return harness, commands

    def test_missing_transitive_pin_fails_before_network(self) -> None:
        harness, commands = self._harness({"node-app": SHA_A})
        with self.assertRaisesRegex(AssertionError, "has no exact commit"):
            harness.source_root("node-lib")
        self.assertEqual(commands, [])

    def test_dependency_fetch_uses_only_the_exact_commit(self) -> None:
        harness, commands = self._harness({"node-app": SHA_B, "node-lib": SHA_A})
        resolved = harness.source_root("node-lib")
        self.assertEqual(resolved, harness.dependency_repos / "node-lib")

        fetch = next(command for command in commands if "fetch" in command)
        self.assertEqual(fetch[-1], SHA_A)
        self.assertIn("--depth", fetch)
        self.assertIn("--no-tags", fetch)
        self.assertNotIn("main", fetch)

        checkout = next(command for command in commands if "checkout" in command)
        self.assertEqual(checkout[-2:], ["--detach", "FETCH_HEAD"])

        command_count = len(commands)
        self.assertEqual(harness.source_root("node-lib"), resolved)
        self.assertEqual(len(commands), command_count, "cached clone should be reused")

    def test_checked_out_dependency_must_match_the_pin(self) -> None:
        harness, _commands = self._harness({"node-app": SHA_A, "node-lib": SHA_A})

        def wrong_head(command, **_kwargs):
            argv = [str(value) for value in command]
            if "rev-parse" in argv:
                return SHA_B + "\n"
            return ""

        harness.run = wrong_head
        with self.assertRaisesRegex(AssertionError, "ref mismatch"):
            harness.source_root("node-lib")

    def test_root_checkout_must_be_present_and_exact(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        fixture = Path(temp.name) / "fixture"
        fixture.mkdir()

        def base_init(instance, args) -> None:
            instance.repo = args.repo
            instance.fixture = args.fixture_dir
            instance.clones = {}
            instance.dependency_repos = Path(temp.name) / "dependencies"

        args = SimpleNamespace(
            repo="node-app",
            fixture_dir=fixture,
            fixture_refs_json={"node-app": SHA_A},
        )
        with patch.object(candidate_lifecycle.lifecycle.Harness, "__init__", base_init), patch.object(
            candidate_lifecycle.PinnedHarness, "run", return_value=SHA_A + "\n"
        ):
            candidate_lifecycle.PinnedHarness(args)

        args.fixture_refs_json = {}
        with patch.object(candidate_lifecycle.lifecycle.Harness, "__init__", base_init):
            with self.assertRaisesRegex(AssertionError, "absent"):
                candidate_lifecycle.PinnedHarness(args)

        args.fixture_refs_json = {"node-app": SHA_A}
        with patch.object(candidate_lifecycle.lifecycle.Harness, "__init__", base_init), patch.object(
            candidate_lifecycle.PinnedHarness, "run", return_value=SHA_B + "\n"
        ):
            with self.assertRaisesRegex(AssertionError, "root fixture ref mismatch"):
                candidate_lifecycle.PinnedHarness(args)


if __name__ == "__main__":
    unittest.main()
