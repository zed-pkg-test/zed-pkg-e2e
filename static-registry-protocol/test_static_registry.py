#!/usr/bin/env python3
"""Focused fail-closed tests for the static registry fixture generator."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILDER = ROOT / "build_static_registry.py"


class StaticRegistryGeneratorSafetyTests(unittest.TestCase):
    def run_builder(self, fixtures: Path, output: Path) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["SOURCE_DATE_EPOCH"] = "0"
        return subprocess.run(
            [
                sys.executable,
                str(BUILDER),
                "--fixtures",
                str(fixtures),
                "--out",
                str(output),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            check=False,
        )

    def package(
        self,
        fixtures: Path,
        *,
        org: str = "zpkg-e2e",
        name: str = "hello-zed",
        version: str = "1.0.0",
    ) -> Path:
        package = fixtures / org / name / version
        package.mkdir(parents=True)
        (package / "zpkg.json").write_text(
            json.dumps({"deps": [], "yanked": False}) + "\n",
            encoding="utf-8",
        )
        (package / "payload.txt").write_text("payload\n", encoding="utf-8")
        return package

    def test_accepts_a_new_or_empty_real_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = root / "fixtures"
            self.package(fixtures)

            new_output = root / "new-output"
            created = self.run_builder(fixtures, new_output)
            self.assertEqual(created.returncode, 0, created.stderr)
            self.assertTrue((new_output / "checkpoint.json").is_file())

            empty_output = root / "empty-output"
            empty_output.mkdir()
            created_in_empty = self.run_builder(fixtures, empty_output)
            self.assertEqual(created_in_empty.returncode, 0, created_in_empty.stderr)
            self.assertTrue((empty_output / "checkpoint.json").is_file())

    def test_rejects_nonempty_output_without_touching_caller_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = root / "fixtures"
            self.package(fixtures)
            output = root / "output"
            output.mkdir()
            sentinel = output / "caller-owned.txt"
            sentinel.write_bytes(b"do not overwrite\n")

            result = self.run_builder(fixtures, output)

            self.assertEqual(result.returncode, 2)
            self.assertIn("must be empty", result.stderr)
            self.assertEqual(sentinel.read_bytes(), b"do not overwrite\n")
            self.assertFalse((output / "checkpoint.json").exists())

    def test_rejects_parent_traversal_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = root / "fixtures"
            self.package(fixtures)
            output = root / "nested" / ".."

            result = self.run_builder(fixtures, output)

            self.assertEqual(result.returncode, 2)
            self.assertIn("without `..`", result.stderr)
            self.assertFalse((root / "checkpoint.json").exists())
            self.assertFalse((root / "nested").exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "platform has no symlink support")
    def test_rejects_fixture_symlinks_instead_of_following_them(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = root / "fixtures"
            package = self.package(fixtures)
            outside = root / "outside-secret.txt"
            outside.write_text("must not be archived\n", encoding="utf-8")
            os.symlink(outside, package / "escape.txt")
            output = root / "output"

            result = self.run_builder(fixtures, output)

            self.assertEqual(result.returncode, 2)
            self.assertIn("symbolic link", result.stderr)
            self.assertFalse((output / "checkpoint.json").exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "platform has no symlink support")
    def test_rejects_symlinked_metadata_before_reading_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = root / "fixtures"
            package = self.package(fixtures)
            metadata = package / "zpkg.json"
            metadata.unlink()
            outside = root / "outside-metadata.json"
            outside.write_text('{"deps":[],"yanked":false}\n', encoding="utf-8")
            os.symlink(outside, metadata)
            output = root / "output"

            result = self.run_builder(fixtures, output)

            self.assertEqual(result.returncode, 2)
            self.assertIn("metadata is a symbolic link", result.stderr)
            self.assertFalse(output.exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "platform has no symlink support")
    def test_later_invalid_package_leaves_no_partial_output_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = root / "fixtures"
            self.package(fixtures, name="a-valid-package")
            invalid = self.package(fixtures, name="z-invalid-package")
            outside = root / "outside-secret.txt"
            outside.write_text("must not be archived\n", encoding="utf-8")
            os.symlink(outside, invalid / "escape.txt")
            output = root / "output"

            result = self.run_builder(fixtures, output)

            self.assertEqual(result.returncode, 2)
            self.assertIn("symbolic link", result.stderr)
            self.assertFalse(
                output.exists(),
                "validation failure after an earlier valid package must not expose a partial registry",
            )
            self.assertEqual(
                list(root.glob(".output.zpkg-static-*")),
                [],
                "validation failure must not leave staging directories",
            )

    @unittest.skipUnless(hasattr(os, "symlink"), "platform has no symlink support")
    def test_rejects_a_symlinked_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = root / "fixtures"
            self.package(fixtures)
            real_output = root / "real-output"
            real_output.mkdir()
            linked_output = root / "linked-output"
            os.symlink(real_output, linked_output, target_is_directory=True)

            result = self.run_builder(fixtures, linked_output)

            self.assertEqual(result.returncode, 2)
            self.assertIn("output path is a symbolic link", result.stderr)
            self.assertEqual(list(real_output.iterdir()), [])

    def test_rejects_non_object_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = root / "fixtures"
            package = self.package(fixtures)
            (package / "zpkg.json").write_text("[]\n", encoding="utf-8")
            output = root / "output"

            result = self.run_builder(fixtures, output)

            self.assertEqual(result.returncode, 2)
            self.assertIn("metadata must be a JSON object", result.stderr)
            self.assertFalse(output.exists())

    def test_rejects_invalid_metadata_field_types(self) -> None:
        for metadata, expected in (
            ({"deps": "not-an-array", "yanked": False}, "`deps` must be an array"),
            ({"deps": [], "yanked": "false"}, "`yanked` must be boolean"),
        ):
            with self.subTest(metadata=metadata):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    fixtures = root / "fixtures"
                    package = self.package(fixtures)
                    (package / "zpkg.json").write_text(
                        json.dumps(metadata) + "\n",
                        encoding="utf-8",
                    )
                    output = root / "output"

                    result = self.run_builder(fixtures, output)

                    self.assertEqual(result.returncode, 2)
                    self.assertIn(expected, result.stderr)
                    self.assertFalse(output.exists())

    def test_rejects_invalid_semantic_version_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = root / "fixtures"
            self.package(fixtures, version="1.0")
            output = root / "output"

            result = self.run_builder(fixtures, output)

            self.assertEqual(result.returncode, 2)
            self.assertIn("invalid v0 semantic version", result.stderr)
            self.assertFalse(output.exists())

    def test_rejects_uppercase_org_segments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = root / "fixtures"
            self.package(fixtures, org="Zpkg-E2E")
            output = root / "output"

            result = self.run_builder(fixtures, output)

            self.assertEqual(result.returncode, 2)
            self.assertIn("lowercase canonical form", result.stderr)
            self.assertFalse((output / "checkpoint.json").exists())

    def test_rejects_an_empty_fixture_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = root / "fixtures"
            fixtures.mkdir()
            output = root / "output"

            result = self.run_builder(fixtures, output)

            self.assertEqual(result.returncode, 2)
            self.assertIn("contains no package versions", result.stderr)
            self.assertFalse((output / "checkpoint.json").exists())


if __name__ == "__main__":
    unittest.main()
