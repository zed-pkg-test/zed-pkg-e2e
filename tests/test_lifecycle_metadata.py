from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "lifecycle.py"
SPEC = importlib.util.spec_from_file_location("zed_lifecycle", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"unable to load lifecycle harness from {SCRIPT}")
LIFECYCLE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = LIFECYCLE
SPEC.loader.exec_module(LIFECYCLE)


class LifecycleMetadataTests(unittest.TestCase):
    def test_shared_schema_is_a_publishable_fixture_and_dependency_source(self) -> None:
        self.assertNotIn("shared-schema", LIFECYCLE.NON_PACKAGE_REPOS)
        self.assertEqual(
            LIFECYCLE.PACKAGE_SOURCES["zedtest/shared-schema"],
            ("shared-schema", "."),
        )

    def test_orchestrator_is_the_only_non_package_repository(self) -> None:
        self.assertEqual(LIFECYCLE.NON_PACKAGE_REPOS, {"zed-pkg-e2e"})

    def test_dependency_sections_are_combined_deduplicated_and_sorted(self) -> None:
        manifest = {
            "dependencies": {
                "zedtest/shared-schema": "^1",
                "zed-pkg-test/python-lib": "^1",
            },
            "build_dependencies": {
                "zedtest/shared-schema": "^1",
                "zed-pkg-test/go-lib": "^1",
            },
            "build-dependencies": {
                "zed-pkg-test/rust-lib": "^1",
            },
        }
        self.assertEqual(
            LIFECYCLE.manifest_dependencies(manifest),
            [
                "zed-pkg-test/go-lib",
                "zed-pkg-test/python-lib",
                "zed-pkg-test/rust-lib",
                "zedtest/shared-schema",
            ],
        )

    def test_every_dependency_source_is_a_repository_path_pair(self) -> None:
        for package, source in LIFECYCLE.PACKAGE_SOURCES.items():
            with self.subTest(package=package):
                self.assertRegex(package, r"^[^/]+/[^/]+$")
                self.assertIsInstance(source, tuple)
                self.assertEqual(len(source), 2)
                self.assertTrue(all(isinstance(value, str) and value for value in source))


if __name__ == "__main__":
    unittest.main()
