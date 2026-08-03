#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE_PATH = REPOSITORY_ROOT / "scripts" / "lifecycle.py"
SPEC = importlib.util.spec_from_file_location("zed_lifecycle_harness", LIFECYCLE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"could not load lifecycle harness from {LIFECYCLE_PATH}")
LIFECYCLE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = LIFECYCLE
SPEC.loader.exec_module(LIFECYCLE)


class LifecycleHarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixture = self.root / "fixture"
        self.fixture.mkdir()
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=self.fixture,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        fake_zed = self.root / "zed"
        fake_zed.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_zed.chmod(0o755)
        args = argparse.Namespace(
            repo="fixture",
            fixture_dir=self.fixture,
            zed=fake_zed,
            work_root=self.root / "work",
        )
        self.harness = LIFECYCLE.Harness(args)

    def test_shared_schema_is_a_known_dependency_source(self) -> None:
        self.assertEqual(
            LIFECYCLE.PACKAGE_SOURCES["zedtest/shared-schema"],
            ("shared-schema", "."),
        )

    def test_untracked_pack_output_is_removed(self) -> None:
        archive = self.fixture / ".zed" / "pack" / "package.tar.gz"
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b"fixture")

        self.harness.remove_generated_pack_output(self.fixture)

        self.assertFalse(archive.exists())
        self.assertFalse((self.fixture / ".zed").exists())

    def test_unrelated_zed_state_is_preserved(self) -> None:
        archive = self.fixture / ".zed" / "pack" / "package.tar.gz"
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b"fixture")
        unrelated = self.fixture / ".zed" / "keep.txt"
        unrelated.write_text("keep\n", encoding="utf-8")

        self.harness.remove_generated_pack_output(self.fixture)

        self.assertFalse(archive.exists())
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep\n")

    def test_tracked_pack_output_is_never_deleted(self) -> None:
        tracked = self.fixture / ".zed" / "pack" / "tracked.tar.gz"
        tracked.parent.mkdir(parents=True)
        tracked.write_bytes(b"tracked")
        subprocess.run(
            ["git", "add", ".zed/pack/tracked.tar.gz"],
            cwd=self.fixture,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        with self.assertRaisesRegex(AssertionError, "tracked publish output"):
            self.harness.remove_generated_pack_output(self.fixture)

        self.assertEqual(tracked.read_bytes(), b"tracked")

    def test_symlinked_zed_directory_is_never_traversed(self) -> None:
        outside = self.root / "outside-zed"
        pack = outside / "pack"
        pack.mkdir(parents=True)
        sentinel = pack / "sentinel.tar.gz"
        sentinel.write_bytes(b"outside")
        os.symlink(outside, self.fixture / ".zed", target_is_directory=True)

        with self.assertRaisesRegex(AssertionError, "symlinked .zed path"):
            self.harness.remove_generated_pack_output(self.fixture)

        self.assertEqual(sentinel.read_bytes(), b"outside")

    def test_symlinked_pack_directory_is_never_removed(self) -> None:
        zed_dir = self.fixture / ".zed"
        zed_dir.mkdir()
        outside = self.root / "outside-pack"
        outside.mkdir()
        sentinel = outside / "sentinel.tar.gz"
        sentinel.write_bytes(b"outside")
        os.symlink(outside, zed_dir / "pack", target_is_directory=True)

        with self.assertRaisesRegex(AssertionError, "symlinked pack path"):
            self.harness.remove_generated_pack_output(self.fixture)

        self.assertEqual(sentinel.read_bytes(), b"outside")

    def test_non_directory_pack_path_fails_closed(self) -> None:
        pack = self.fixture / ".zed" / "pack"
        pack.parent.mkdir()
        pack.write_bytes(b"not-a-directory")

        with self.assertRaisesRegex(AssertionError, "not a directory"):
            self.harness.remove_generated_pack_output(self.fixture)

        self.assertEqual(pack.read_bytes(), b"not-a-directory")


if __name__ == "__main__":
    unittest.main(verbosity=2)
