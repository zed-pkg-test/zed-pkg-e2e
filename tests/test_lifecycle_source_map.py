#!/usr/bin/env python3
"""Contract tests for the lifecycle fixture package source registry."""

from __future__ import annotations

import importlib.util
import os
import sys
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
