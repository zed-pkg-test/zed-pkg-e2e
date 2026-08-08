from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

import org_fleet_inventory as fleet


def core_inventory() -> dict[str, fleet.CoreSpec]:
    return {
        "node-lib": fleet.CoreSpec("node-lib", "package", ".zpkg.toml"),
        "zed-pkg-e2e": fleet.CoreSpec("zed-pkg-e2e", "orchestrator", None),
    }


def probe(
    repository: fleet.LiveRepository,
    path: str,
) -> fleet.ManifestProbe:
    if repository.name in {"zed-pkg-e2e", ".github", "api-contract-e2e"}:
        return fleet.ManifestProbe(repository.name, path, False)
    return fleet.ManifestProbe(
        repository.name,
        path,
        True,
        "zed-pkg-test",
        repository.name,
        "0.1.0",
    )


class FakeResponse:
    def __init__(self, payload: object):
        self._payload = json.dumps(payload).encode()
        self.headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._payload


class CoreInventoryTests(unittest.TestCase):
    def write_inventory(self, raw: object) -> Path:
        temp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
        json.dump(raw, temp)
        temp.close()
        self.addCleanup(Path(temp.name).unlink)
        return Path(temp.name)

    def valid_raw(self) -> dict[str, object]:
        return {
            "schema": fleet.CORE_SCHEMA,
            "organization": "zed-pkg-test",
            "orchestrator": "zed-pkg-e2e",
            "repositories": [
                {"name": "node-lib", "kind": "package", "manifest": ".zpkg.toml"},
                {"name": "zed-pkg-e2e", "kind": "orchestrator", "manifest": None},
            ],
        }

    def test_loads_valid_core_inventory(self):
        organization, result = fleet.load_core_inventory(self.write_inventory(self.valid_raw()))
        self.assertEqual(organization, "zed-pkg-test")
        self.assertTrue(result["node-lib"].requires_manifest)
        self.assertFalse(result["zed-pkg-e2e"].requires_manifest)

    def test_rejects_unknown_schema(self):
        raw = self.valid_raw()
        raw["schema"] = "wrong"
        with self.assertRaisesRegex(fleet.FleetError, "schema"):
            fleet.load_core_inventory(self.write_inventory(raw))

    def test_rejects_duplicate_repository(self):
        raw = self.valid_raw()
        raw["repositories"].append(raw["repositories"][0])
        with self.assertRaisesRegex(fleet.FleetError, "duplicate"):
            fleet.load_core_inventory(self.write_inventory(raw))

    def test_rejects_package_without_manifest(self):
        raw = self.valid_raw()
        raw["repositories"][0]["manifest"] = None
        with self.assertRaisesRegex(fleet.FleetError, "manifest"):
            fleet.load_core_inventory(self.write_inventory(raw))

    def test_rejects_orchestrator_with_manifest(self):
        raw = self.valid_raw()
        raw["repositories"][1]["manifest"] = ".zpkg.toml"
        with self.assertRaisesRegex(fleet.FleetError, "must be null"):
            fleet.load_core_inventory(self.write_inventory(raw))

    def test_rejects_manifest_path_escape(self):
        raw = self.valid_raw()
        raw["repositories"][0]["manifest"] = "../outside.toml"
        with self.assertRaisesRegex(fleet.FleetError, "escapes"):
            fleet.load_core_inventory(self.write_inventory(raw))

    def test_rejects_second_orchestrator(self):
        raw = self.valid_raw()
        raw["repositories"].append(
            {"name": "other", "kind": "orchestrator", "manifest": None}
        )
        with self.assertRaisesRegex(fleet.FleetError, "exactly"):
            fleet.load_core_inventory(self.write_inventory(raw))


class ClassificationTests(unittest.TestCase):
    def test_classifies_core_package(self):
        self.assertEqual(fleet.classify_repository("node-lib", core_inventory()), "core-package")

    def test_classifies_core_orchestrator(self):
        self.assertEqual(
            fleet.classify_repository("zed-pkg-e2e", core_inventory()),
            "core-orchestrator",
        )

    def test_classifies_governance_repository(self):
        self.assertEqual(fleet.classify_repository(".github", core_inventory()), "governance")

    def test_classifies_e2e_harness(self):
        self.assertEqual(
            fleet.classify_repository("offline-cache-e2e", core_inventory()),
            "harness",
        )

    def test_classifies_contract_harness(self):
        self.assertEqual(
            fleet.classify_repository("registry-api-contract", core_inventory()),
            "harness",
        )

    def test_classifies_other_repositories_as_packages(self):
        self.assertEqual(
            fleet.classify_repository("csharp-app", core_inventory()),
            "supplemental-package",
        )

    def test_harness_manifest_is_optional(self):
        path, required = fleet.manifest_requirement(
            fleet.LiveRepository("browser-e2e"), core_inventory()
        )
        self.assertEqual(path, ".zpkg.toml")
        self.assertFalse(required)

    def test_supplemental_package_manifest_is_required(self):
        _, required = fleet.manifest_requirement(
            fleet.LiveRepository("swift-app"), core_inventory()
        )
        self.assertTrue(required)


class AuditTests(unittest.TestCase):
    def healthy_repositories(self):
        return [
            fleet.LiveRepository("node-lib"),
            fleet.LiveRepository("zed-pkg-e2e"),
            fleet.LiveRepository(".github"),
            fleet.LiveRepository("api-contract-e2e"),
            fleet.LiveRepository("csharp-app"),
        ]

    def test_accepts_core_and_supplemental_fleet(self):
        report = fleet.audit_fleet(
            "zed-pkg-test", core_inventory(), self.healthy_repositories(), probe
        )
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["live_repository_count"], 5)
        self.assertEqual(report["supplemental_repository_count"], 3)
        self.assertEqual(report["required_manifest_count"], 2)
        self.assertEqual(report["required_manifest_present_count"], 2)
        self.assertEqual(report["manifest_present_count"], 2)

    def test_reports_missing_core_repository(self):
        report = fleet.audit_fleet(
            "zed-pkg-test",
            core_inventory(),
            [fleet.LiveRepository("node-lib")],
            probe,
        )
        self.assertIn("zed-pkg-e2e", report["missing_core_repositories"])
        self.assertTrue(any("missing from public org" in error for error in report["errors"]))

    def test_reports_missing_required_supplemental_manifest(self):
        def missing(repo, path):
            return fleet.ManifestProbe(repo.name, path, False)

        report = fleet.audit_fleet(
            "zed-pkg-test",
            core_inventory(),
            [
                fleet.LiveRepository("node-lib"),
                fleet.LiveRepository("zed-pkg-e2e"),
                fleet.LiveRepository("java-app"),
            ],
            missing,
        )
        self.assertTrue(any("java-app: required manifest is missing" in error for error in report["errors"]))

    def test_allows_manifestless_harness(self):
        report = fleet.audit_fleet(
            "zed-pkg-test",
            core_inventory(),
            [
                fleet.LiveRepository("node-lib"),
                fleet.LiveRepository("zed-pkg-e2e"),
                fleet.LiveRepository("offline-cache-e2e"),
            ],
            probe,
        )
        self.assertEqual(report["errors"], [])

    def test_reports_manifest_org_mismatch(self):
        def wrong_org(repo, path):
            return fleet.ManifestProbe(repo.name, path, True, "wrong", repo.name, "1.0.0")

        report = fleet.audit_fleet(
            "zed-pkg-test", core_inventory(), self.healthy_repositories()[:2], wrong_org
        )
        self.assertTrue(any("package.org mismatch" in error for error in report["errors"]))

    def test_reports_manifest_name_mismatch(self):
        def wrong_name(repo, path):
            return fleet.ManifestProbe(repo.name, path, True, "zed-pkg-test", "wrong", "1.0.0")

        report = fleet.audit_fleet(
            "zed-pkg-test", core_inventory(), self.healthy_repositories()[:2], wrong_name
        )
        self.assertTrue(any("package.name mismatch" in error for error in report["errors"]))

    def test_reports_repository_shape_failures(self):
        bad = fleet.LiveRepository(
            "node-lib",
            private=True,
            archived=True,
            disabled=True,
            fork=True,
            default_branch="master",
        )
        report = fleet.audit_fleet(
            "zed-pkg-test",
            core_inventory(),
            [bad, fleet.LiveRepository("zed-pkg-e2e")],
            probe,
        )
        joined = "\n".join(report["errors"])
        for fragment in (
            "must be public",
            "must not be archived",
            "must not be disabled",
            "must not be a fork",
            "default branch",
        ):
            self.assertIn(fragment, joined)

    def test_reports_duplicate_live_repository(self):
        report = fleet.audit_fleet(
            "zed-pkg-test",
            core_inventory(),
            [
                fleet.LiveRepository("node-lib"),
                fleet.LiveRepository("node-lib"),
                fleet.LiveRepository("zed-pkg-e2e"),
            ],
            probe,
        )
        self.assertTrue(any("duplicate live repositories" in error for error in report["errors"]))

    def test_converts_manifest_loader_error_to_repository_error(self):
        def broken(_repo, _path):
            raise fleet.FleetError("bad manifest payload")

        report = fleet.audit_fleet(
            "zed-pkg-test",
            core_inventory(),
            [fleet.LiveRepository("node-lib"), fleet.LiveRepository("zed-pkg-e2e")],
            broken,
        )
        self.assertTrue(any("bad manifest payload" in error for error in report["errors"]))


class ManifestDecodeTests(unittest.TestCase):
    def payload(self, text: str) -> dict[str, object]:
        return {
            "type": "file",
            "encoding": "base64",
            "content": base64.b64encode(text.encode()).decode(),
        }

    def test_decodes_valid_manifest(self):
        result = fleet.decode_manifest(
            "java-app",
            ".zpkg.toml",
            self.payload('[package]\norg="zed-pkg-test"\nname="java-app"\nversion="1.2.3"\n'),
        )
        self.assertTrue(result.present)
        self.assertEqual(result.package_name, "java-app")
        self.assertEqual(result.version, "1.2.3")

    def test_rejects_invalid_toml(self):
        with self.assertRaisesRegex(fleet.FleetError, "invalid TOML"):
            fleet.decode_manifest("x", ".zpkg.toml", self.payload("[package"))

    def test_rejects_missing_package_table(self):
        with self.assertRaisesRegex(fleet.FleetError, r"missing \[package\]"):
            fleet.decode_manifest("x", ".zpkg.toml", self.payload("value=1\n"))

    def test_rejects_non_file_payload(self):
        with self.assertRaisesRegex(fleet.FleetError, "regular file"):
            fleet.decode_manifest(
                "x",
                ".zpkg.toml",
                {"type": "dir", "encoding": "base64", "content": ""},
            )


class HttpTests(unittest.TestCase):
    def test_fetches_public_repositories(self):
        payload = [
            {
                "name": "node-lib",
                "private": False,
                "archived": False,
                "disabled": False,
                "fork": False,
                "default_branch": "main",
            }
        ]
        result = fleet.fetch_public_repositories(
            "zed-pkg-test", opener=lambda _request: FakeResponse(payload)
        )
        self.assertEqual(result, (fleet.LiveRepository("node-lib"),))

    def test_manifest_loader_maps_404_to_absent_probe(self):
        def not_found(request):
            raise urllib.error.HTTPError(
                request.full_url, 404, "not found", {}, io.BytesIO(b"{}")
            )

        loader = fleet.make_github_manifest_loader("zed-pkg-test", opener=not_found)
        result = loader(fleet.LiveRepository("missing"), ".zpkg.toml")
        self.assertFalse(result.present)

    def test_manifest_loader_surfaces_non_404_http_errors(self):
        def forbidden(request):
            raise urllib.error.HTTPError(
                request.full_url, 403, "forbidden", {}, io.BytesIO(b"{}")
            )

        loader = fleet.make_github_manifest_loader("zed-pkg-test", opener=forbidden)
        with self.assertRaisesRegex(fleet.FleetError, "403"):
            loader(fleet.LiveRepository("private"), ".zpkg.toml")


if __name__ == "__main__":
    unittest.main()
