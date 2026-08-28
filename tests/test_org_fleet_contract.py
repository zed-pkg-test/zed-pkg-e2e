from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import org_fleet_contract as contract
import org_fleet_inventory as fleet


class PackageOrganizationTests(unittest.TestCase):
    def write_inventory(self, package_organizations: object = None) -> Path:
        raw: dict[str, object] = {
            "organization": "zed-pkg-test",
            "package_organizations": (
                ["zed-pkg-test", "zedtest"]
                if package_organizations is None
                else package_organizations
            ),
        }
        temp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
        json.dump(raw, temp)
        temp.close()
        path = Path(temp.name)
        self.addCleanup(path.unlink)
        return path

    def test_loads_reviewed_package_organizations_in_order(self):
        result = contract.load_package_organizations(
            self.write_inventory(), "zed-pkg-test"
        )
        self.assertEqual(result, ("zed-pkg-test", "zedtest"))

    def test_rejects_missing_package_organizations(self):
        path = self.write_inventory([])
        with self.assertRaisesRegex(fleet.FleetError, "non-empty array"):
            contract.load_package_organizations(path, "zed-pkg-test")

    def test_rejects_non_string_package_organization(self):
        path = self.write_inventory(["zed-pkg-test", 42])
        with self.assertRaisesRegex(fleet.FleetError, "non-empty string"):
            contract.load_package_organizations(path, "zed-pkg-test")

    def test_rejects_duplicate_package_organization(self):
        path = self.write_inventory(["zed-pkg-test", "zed-pkg-test"])
        with self.assertRaisesRegex(fleet.FleetError, "duplicate"):
            contract.load_package_organizations(path, "zed-pkg-test")

    def test_requires_github_organization_in_reviewed_namespaces(self):
        path = self.write_inventory(["zedtest"])
        with self.assertRaisesRegex(fleet.FleetError, "must include"):
            contract.load_package_organizations(path, "zed-pkg-test")


class AliasAwareAuditTests(unittest.TestCase):
    def core(self) -> dict[str, fleet.CoreSpec]:
        return {
            "legacy-lib": fleet.CoreSpec("legacy-lib", "package", ".zpkg.toml"),
            "zed-pkg-e2e": fleet.CoreSpec("zed-pkg-e2e", "orchestrator", None),
        }

    def repositories(self) -> list[fleet.LiveRepository]:
        return [
            fleet.LiveRepository("legacy-lib"),
            fleet.LiveRepository("zed-pkg-e2e"),
        ]

    def loader_for(self, package_org: str):
        def load(repo: fleet.LiveRepository, path: str) -> fleet.ManifestProbe:
            if repo.name == "zed-pkg-e2e":
                return fleet.ManifestProbe(repo.name, path, False)
            return fleet.ManifestProbe(
                repo.name,
                path,
                True,
                package_org,
                repo.name,
                "1.0.0",
            )

        return load

    def test_accepts_legacy_namespace_and_preserves_it_in_evidence(self):
        report = contract.audit_with_package_organizations(
            "zed-pkg-test",
            ("zed-pkg-test", "zedtest"),
            self.core(),
            self.repositories(),
            self.loader_for("zedtest"),
        )
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["schema"], contract.REPORT_SCHEMA)
        self.assertEqual(
            report["package_organizations"], ["zed-pkg-test", "zedtest"]
        )
        legacy = next(row for row in report["repositories"] if row["name"] == "legacy-lib")
        self.assertEqual(legacy["manifest"]["package_org"], "zedtest")
        self.assertTrue(legacy["manifest"]["package_org_allowed"])

    def test_accepts_current_namespace(self):
        report = contract.audit_with_package_organizations(
            "zed-pkg-test",
            ("zed-pkg-test", "zedtest"),
            self.core(),
            self.repositories(),
            self.loader_for("zed-pkg-test"),
        )
        self.assertEqual(report["errors"], [])

    def test_rejects_unreviewed_namespace(self):
        report = contract.audit_with_package_organizations(
            "zed-pkg-test",
            ("zed-pkg-test", "zedtest"),
            self.core(),
            self.repositories(),
            self.loader_for("unreviewed"),
        )
        self.assertTrue(any("package.org mismatch" in error for error in report["errors"]))
        legacy = next(row for row in report["repositories"] if row["name"] == "legacy-lib")
        self.assertEqual(legacy["manifest"]["package_org"], "unreviewed")
        self.assertFalse(legacy["manifest"]["package_org_allowed"])

    def test_manifestless_orchestrator_has_no_namespace_decision(self):
        report = contract.audit_with_package_organizations(
            "zed-pkg-test",
            ("zed-pkg-test", "zedtest"),
            self.core(),
            self.repositories(),
            self.loader_for("zedtest"),
        )
        orchestrator = next(
            row for row in report["repositories"] if row["name"] == "zed-pkg-e2e"
        )
        self.assertIsNone(orchestrator["manifest"]["package_org_allowed"])


if __name__ == "__main__":
    unittest.main()
