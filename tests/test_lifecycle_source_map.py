#!/usr/bin/env python3
"""Contract tests for lifecycle package sources and transient outputs."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE_PATH = ROOT / "scripts" / "lifecycle.py"
FIXTURE_ROOT = os.environ.get("FIXTURE_ROOT")


def load_lifecycle() -> ModuleType:
    spec = importlib.util.spec_from_file_location("zed_lifecycle_contract", LIFECYCLE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load lifecycle module from {LIFECYCLE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


lifecycle = load_lifecycle()


class SourceMapShapeTests(unittest.TestCase):
    def test_shared_schema_is_a_package_source(self) -> None:
        self.assertNotIn("shared-schema", lifecycle.NON_PACKAGE_REPOS)
        self.assertEqual(
            lifecycle.PACKAGE_SOURCES.get("zedtest/shared-schema"),
            ("shared-schema", "."),
        )

    def test_package_repositories_are_not_classified_as_non_packages(self) -> None:
        conflicts = {
            package: repo
            for package, (repo, _relative) in lifecycle.PACKAGE_SOURCES.items()
            if repo in lifecycle.NON_PACKAGE_REPOS
        }
        self.assertEqual(conflicts, {})

    def test_source_map_keys_and_paths_are_normalized(self) -> None:
        for package, (repo, relative) in lifecycle.PACKAGE_SOURCES.items():
            with self.subTest(package=package):
                self.assertRegex(package, r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
                self.assertRegex(repo, r"^[A-Za-z0-9_.-]+$")
                self.assertFalse(Path(relative).is_absolute())
                self.assertNotIn("..", Path(relative).parts)


class TransientPackCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Lifecycle Contract")
        self.git("config", "user.email", "lifecycle-contract@example.invalid")
        (self.root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        self.git("add", "tracked.txt")
        self.git("commit", "-qm", "initial fixture")
        self.package = lifecycle.PackageRef("zedtest/shared-schema", "1.0.0")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

    def pack_path(self, name: str) -> Path:
        path = self.root / ".zed" / "pack" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def test_archive_name_matches_the_cli_package_identity_convention(self) -> None:
        self.assertEqual(
            lifecycle.publish_archive_name(self.package),
            "zedtest-shared-schema-1.0.0.tar.gz",
        )

    def test_removes_only_the_expected_untracked_publish_archive(self) -> None:
        archive = self.pack_path(lifecycle.publish_archive_name(self.package))
        archive.write_bytes(b"archive")

        lifecycle.remove_transient_pack_outputs(self.root, [self.package])

        self.assertFalse(archive.exists())
        status = self.git("status", "--porcelain=v1", "--untracked-files=all")
        self.assertEqual(status.stdout, "")

    def test_refuses_an_unexpected_untracked_pack_file(self) -> None:
        unexpected = self.pack_path("unexpected.tar.gz")
        unexpected.write_bytes(b"unexpected")

        with self.assertRaisesRegex(AssertionError, "unexpected pack output"):
            lifecycle.remove_transient_pack_outputs(self.root, [self.package])

        self.assertTrue(unexpected.is_file())

    def test_refuses_to_delete_a_tracked_pack_archive(self) -> None:
        archive = self.pack_path(lifecycle.publish_archive_name(self.package))
        archive.write_bytes(b"tracked archive")
        self.git("add", archive.relative_to(self.root).as_posix())
        self.git("commit", "-qm", "track archive")
        archive.write_bytes(b"modified archive")

        with self.assertRaisesRegex(AssertionError, "tracked or modified"):
            lifecycle.remove_transient_pack_outputs(self.root, [self.package])

        self.assertEqual(archive.read_bytes(), b"modified archive")


@unittest.skipUnless(FIXTURE_ROOT, "FIXTURE_ROOT is required for live fixture checks")
class LiveFixtureContractTests(unittest.TestCase):
    fixture_root = Path(FIXTURE_ROOT or ".").resolve()

    def source_for_dependency(self, dependency: str) -> Path:
        try:
            repo, relative = lifecycle.PACKAGE_SOURCES[dependency]
        except KeyError as error:
            self.fail(f"dependency {dependency!r} has no PACKAGE_SOURCES entry: {error}")
        source = (self.fixture_root / repo / relative).resolve()
        self.assertTrue(
            source.is_relative_to(self.fixture_root),
            f"mapped source escapes fixture root: {source}",
        )
        self.assertTrue((source / ".zpkg.toml").is_file(), source)
        return source

    def test_python_app_dependencies_resolve_to_matching_package_outputs(self) -> None:
        python_app = self.fixture_root / "python-app"
        manifest = lifecycle.read_manifest(python_app)
        dependencies = lifecycle.manifest_dependencies(manifest)
        self.assertIn("zedtest/shared-schema", dependencies)
        self.assertIn("zedtest/polyglot-lib-python", dependencies)

        for dependency in dependencies:
            with self.subTest(dependency=dependency):
                source = self.source_for_dependency(dependency)
                outputs = {
                    package.full_name
                    for package in lifecycle.expected_packages(
                        lifecycle.read_manifest(source)
                    )
                }
                self.assertIn(dependency, outputs)

    def test_shared_schema_manifest_identity_matches_source_map_key(self) -> None:
        source = self.source_for_dependency("zedtest/shared-schema")
        outputs = lifecycle.expected_packages(lifecycle.read_manifest(source))
        self.assertEqual(
            [(package.full_name, package.version) for package in outputs],
            [("zedtest/shared-schema", "1.0.0")],
        )


if __name__ == "__main__":
    unittest.main()
