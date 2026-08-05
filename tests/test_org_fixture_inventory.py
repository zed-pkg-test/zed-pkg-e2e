from __future__ import annotations

import base64
import importlib.util
import json
import sys
import tempfile
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "org_fixture_inventory.py"
SPEC = importlib.util.spec_from_file_location("org_fixture_inventory", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load inventory module from {SCRIPT}")
inventory = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = inventory
SPEC.loader.exec_module(inventory)


def make_inventory_payload(
    repositories: list[dict[str, object]] | None = None,
    *,
    orchestrator: str = "zed-pkg-e2e",
) -> dict[str, object]:
    return {
        "schema": inventory.INVENTORY_SCHEMA,
        "organization": "zed-pkg-test",
        "orchestrator": orchestrator,
        "repositories": repositories
        or [
            {"name": "node-lib", "kind": "package", "manifest": ".zpkg.toml"},
            {
                "name": "zed-pkg-e2e",
                "kind": "orchestrator",
                "manifest": None,
            },
        ],
    }


def load_payload(payload: dict[str, object]) -> inventory.Inventory:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "inventory.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return inventory.load_inventory(path)


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class InventoryParsingTests(unittest.TestCase):
    def test_loads_valid_inventory(self) -> None:
        loaded = load_payload(make_inventory_payload())
        self.assertEqual(loaded.organization, "zed-pkg-test")
        self.assertEqual(loaded.orchestrator, "zed-pkg-e2e")
        self.assertEqual(loaded.names, ("node-lib", "zed-pkg-e2e"))
        self.assertEqual(loaded.package_names, ("node-lib",))

    def test_rejects_unknown_schema(self) -> None:
        payload = make_inventory_payload()
        payload["schema"] = "unknown"
        with self.assertRaisesRegex(inventory.InventoryError, "schema"):
            load_payload(payload)

    def test_rejects_duplicate_repository_names(self) -> None:
        entries = [
            {"name": "node-lib", "kind": "package", "manifest": ".zpkg.toml"},
            {"name": "node-lib", "kind": "package", "manifest": ".zpkg.toml"},
            {
                "name": "zed-pkg-e2e",
                "kind": "orchestrator",
                "manifest": None,
            },
        ]
        with self.assertRaisesRegex(inventory.InventoryError, "duplicate"):
            load_payload(make_inventory_payload(entries))

    def test_rejects_invalid_repository_names(self) -> None:
        entries = [
            {"name": "../node-lib", "kind": "package", "manifest": ".zpkg.toml"},
            {
                "name": "zed-pkg-e2e",
                "kind": "orchestrator",
                "manifest": None,
            },
        ]
        with self.assertRaisesRegex(inventory.InventoryError, "invalid repository"):
            load_payload(make_inventory_payload(entries))

    def test_rejects_unknown_repository_kind(self) -> None:
        entries = [
            {"name": "node-lib", "kind": "sample", "manifest": ".zpkg.toml"},
            {
                "name": "zed-pkg-e2e",
                "kind": "orchestrator",
                "manifest": None,
            },
        ]
        with self.assertRaisesRegex(inventory.InventoryError, "invalid kind"):
            load_payload(make_inventory_payload(entries))

    def test_rejects_package_without_manifest(self) -> None:
        entries = [
            {"name": "node-lib", "kind": "package", "manifest": None},
            {
                "name": "zed-pkg-e2e",
                "kind": "orchestrator",
                "manifest": None,
            },
        ]
        with self.assertRaisesRegex(inventory.InventoryError, "manifest"):
            load_payload(make_inventory_payload(entries))

    def test_rejects_manifest_path_escape(self) -> None:
        entries = [
            {"name": "node-lib", "kind": "package", "manifest": "../.zpkg.toml"},
            {
                "name": "zed-pkg-e2e",
                "kind": "orchestrator",
                "manifest": None,
            },
        ]
        with self.assertRaisesRegex(inventory.InventoryError, "escapes"):
            load_payload(make_inventory_payload(entries))

    def test_rejects_orchestrator_with_manifest(self) -> None:
        entries = [
            {"name": "node-lib", "kind": "package", "manifest": ".zpkg.toml"},
            {
                "name": "zed-pkg-e2e",
                "kind": "orchestrator",
                "manifest": ".zpkg.toml",
            },
        ]
        with self.assertRaisesRegex(inventory.InventoryError, "must be null"):
            load_payload(make_inventory_payload(entries))

    def test_rejects_mismatched_orchestrator_classification(self) -> None:
        entries = [
            {"name": "node-lib", "kind": "package", "manifest": ".zpkg.toml"},
            {
                "name": "other-orchestrator",
                "kind": "orchestrator",
                "manifest": None,
            },
        ]
        with self.assertRaisesRegex(inventory.InventoryError, "exactly"):
            load_payload(make_inventory_payload(entries))


class LifecycleMatrixParsingTests(unittest.TestCase):
    def test_extracts_repo_matrix_in_declared_order(self) -> None:
        workflow = """
jobs:
  lifecycle:
    strategy:
      matrix:
        repo:
          - node-lib
          - rust-lib
          - zed-pkg-e2e
    steps:
      - run: echo ok
"""
        self.assertEqual(
            inventory.extract_lifecycle_repositories(workflow),
            ("node-lib", "rust-lib", "zed-pkg-e2e"),
        )

    def test_rejects_missing_repo_matrix(self) -> None:
        with self.assertRaisesRegex(inventory.InventoryError, "cannot find"):
            inventory.extract_lifecycle_repositories("jobs: {}\n")

    def test_rejects_duplicate_repo_matrix_entries(self) -> None:
        workflow = """
jobs:
  lifecycle:
    strategy:
      matrix:
        repo:
          - node-lib
          - node-lib
"""
        with self.assertRaisesRegex(inventory.InventoryError, "duplicates"):
            inventory.extract_lifecycle_repositories(workflow)

    def test_rejects_dynamic_repo_matrix_entries(self) -> None:
        workflow = """
jobs:
  lifecycle:
    strategy:
      matrix:
        repo:
          - node-lib
          - ${{ fromJSON(needs.plan.outputs.repos) }}
"""
        with self.assertRaisesRegex(inventory.InventoryError, "unsupported"):
            inventory.extract_lifecycle_repositories(workflow)


class StaticInventoryValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.loaded = load_payload(make_inventory_payload())
        self.sources = {"zed-pkg-test/node-lib": ("node-lib", ".")}

    def test_accepts_matching_inventory_matrix_and_sources(self) -> None:
        errors = inventory.validate_static_inventory(
            self.loaded,
            self.loaded.names,
            self.sources,
            {"zed-pkg-e2e"},
        )
        self.assertEqual(errors, [])

    def test_reports_fixture_missing_from_matrix(self) -> None:
        errors = inventory.validate_static_inventory(
            self.loaded,
            ("node-lib",),
            self.sources,
            {"zed-pkg-e2e"},
        )
        self.assertTrue(any("missing from lifecycle" in error for error in errors))

    def test_reports_unknown_matrix_fixture(self) -> None:
        errors = inventory.validate_static_inventory(
            self.loaded,
            (*self.loaded.names, "unknown"),
            self.sources,
            {"zed-pkg-e2e"},
        )
        self.assertTrue(any("unknown repositories" in error for error in errors))

    def test_reports_non_package_classification_drift(self) -> None:
        errors = inventory.validate_static_inventory(
            self.loaded,
            self.loaded.names,
            self.sources,
            {"node-lib"},
        )
        self.assertTrue(any("NON_PACKAGE_REPOS drift" in error for error in errors))

    def test_reports_unknown_package_source_repository(self) -> None:
        errors = inventory.validate_static_inventory(
            self.loaded,
            self.loaded.names,
            {"zedtest/ghost": ("ghost", ".")},
            {"zed-pkg-e2e"},
        )
        self.assertTrue(any("unclassified" in error for error in errors))

    def test_reports_escaping_package_source_path(self) -> None:
        errors = inventory.validate_static_inventory(
            self.loaded,
            self.loaded.names,
            {"zed-pkg-test/node-lib": ("node-lib", "../outside")},
            {"zed-pkg-e2e"},
        )
        self.assertTrue(any("escapes repository" in error for error in errors))

    def test_reports_orchestrator_as_package_source(self) -> None:
        errors = inventory.validate_static_inventory(
            self.loaded,
            self.loaded.names,
            {"zedtest/bad": ("zed-pkg-e2e", ".")},
            {"zed-pkg-e2e"},
        )
        self.assertTrue(any("orchestrator" in error for error in errors))


class LiveRepositoryValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.loaded = load_payload(make_inventory_payload())
        self.good = (
            inventory.LiveRepository(
                "node-lib", False, False, False, False, "main"
            ),
            inventory.LiveRepository(
                "zed-pkg-e2e", False, False, False, False, "main"
            ),
        )

    def test_accepts_matching_live_repositories(self) -> None:
        self.assertEqual(
            inventory.validate_live_repositories(self.loaded, self.good),
            [],
        )

    def test_reports_missing_live_repository(self) -> None:
        errors = inventory.validate_live_repositories(self.loaded, self.good[:1])
        self.assertTrue(any("missing from org" in error for error in errors))

    def test_reports_unclassified_live_repository(self) -> None:
        extra = inventory.LiveRepository(
            "new-fixture", False, False, False, False, "main"
        )
        errors = inventory.validate_live_repositories(
            self.loaded, (*self.good, extra)
        )
        self.assertTrue(any("unclassified live" in error for error in errors))

    def test_reports_private_repository(self) -> None:
        bad = (
            inventory.LiveRepository("node-lib", True, False, False, False, "main"),
            self.good[1],
        )
        errors = inventory.validate_live_repositories(self.loaded, bad)
        self.assertTrue(any("must be public" in error for error in errors))

    def test_reports_archived_repository(self) -> None:
        bad = (
            inventory.LiveRepository("node-lib", False, True, False, False, "main"),
            self.good[1],
        )
        errors = inventory.validate_live_repositories(self.loaded, bad)
        self.assertTrue(any("archived" in error for error in errors))

    def test_reports_disabled_repository(self) -> None:
        bad = (
            inventory.LiveRepository("node-lib", False, False, True, False, "main"),
            self.good[1],
        )
        errors = inventory.validate_live_repositories(self.loaded, bad)
        self.assertTrue(any("disabled" in error for error in errors))

    def test_reports_fork_repository(self) -> None:
        bad = (
            inventory.LiveRepository("node-lib", False, False, False, True, "main"),
            self.good[1],
        )
        errors = inventory.validate_live_repositories(self.loaded, bad)
        self.assertTrue(any("must not be a fork" in error for error in errors))

    def test_reports_non_main_default_branch(self) -> None:
        bad = (
            inventory.LiveRepository(
                "node-lib", False, False, False, False, "master"
            ),
            self.good[1],
        )
        errors = inventory.validate_live_repositories(self.loaded, bad)
        self.assertTrue(any("default branch" in error for error in errors))


class ManifestProbeTests(unittest.TestCase):
    def manifest_payload(self, content: str) -> dict[str, object]:
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        return {"type": "file", "encoding": "base64", "content": encoded}

    def test_decodes_valid_package_manifest(self) -> None:
        manifest = inventory.decode_manifest_payload(
            "node-lib",
            ".zpkg.toml",
            self.manifest_payload(
                '[package]\norg="zed-pkg-test"\nname="node-lib"\nversion="1.0.0"\n'
            ),
        )
        self.assertEqual(manifest["package"]["name"], "node-lib")

    def test_rejects_manifest_without_package_table(self) -> None:
        with self.assertRaisesRegex(inventory.InventoryError, r"\[package\]"):
            inventory.decode_manifest_payload(
                "node-lib",
                ".zpkg.toml",
                self.manifest_payload("[workspace]\nmembers=[]\n"),
            )

    def test_rejects_invalid_toml(self) -> None:
        with self.assertRaisesRegex(inventory.InventoryError, "invalid TOML"):
            inventory.decode_manifest_payload(
                "node-lib",
                ".zpkg.toml",
                self.manifest_payload("[package\n"),
            )

    def test_fetch_manifest_probe_reports_identity(self) -> None:
        payload = self.manifest_payload(
            '[package]\norg="zedtest"\nname="node-lib"\nversion="2.3.4"\n'
        )
        opener = lambda _request: FakeResponse(payload)
        probe = inventory.fetch_manifest_probe(
            "zed-pkg-test",
            inventory.RepositorySpec("node-lib", "package", ".zpkg.toml"),
            opener=opener,
        )
        self.assertEqual(probe.package, "zedtest/node-lib")
        self.assertEqual(probe.version, "2.3.4")
        self.assertTrue(probe.present)

    def test_fetch_json_wraps_http_errors(self) -> None:
        def opener(request: object) -> object:
            raise urllib.error.HTTPError(
                "https://api.github.test",
                403,
                "forbidden",
                {},
                BytesIO(b'{"message":"rate limited"}'),
            )

        with self.assertRaisesRegex(inventory.InventoryError, "403"):
            inventory.fetch_json(
                "https://api.github.test",
                opener=opener,
            )


class CheckedInContractTests(unittest.TestCase):
    def test_checked_in_inventory_matches_lifecycle_contract(self) -> None:
        loaded = inventory.load_inventory(ROOT / "fixtures" / "org-repositories.json")
        matrix = inventory.extract_lifecycle_repositories(
            (ROOT / ".github" / "workflows" / "lifecycle.yml").read_text(
                encoding="utf-8"
            )
        )
        lifecycle = inventory.load_lifecycle_module(ROOT / "scripts" / "lifecycle.py")
        errors = inventory.validate_static_inventory(
            loaded,
            matrix,
            lifecycle.PACKAGE_SOURCES,
            lifecycle.NON_PACKAGE_REPOS,
        )
        self.assertEqual(errors, [])

    def test_checked_in_inventory_has_twenty_two_unique_repositories(self) -> None:
        loaded = inventory.load_inventory(ROOT / "fixtures" / "org-repositories.json")
        self.assertEqual(len(loaded.names), 22)
        self.assertEqual(len(set(loaded.names)), 22)
        self.assertEqual(len(loaded.package_names), 21)

    def test_orchestrator_is_the_only_manifestless_repository(self) -> None:
        loaded = inventory.load_inventory(ROOT / "fixtures" / "org-repositories.json")
        manifestless = [repo.name for repo in loaded.repositories if repo.manifest is None]
        self.assertEqual(manifestless, ["zed-pkg-e2e"])


if __name__ == "__main__":
    unittest.main()
