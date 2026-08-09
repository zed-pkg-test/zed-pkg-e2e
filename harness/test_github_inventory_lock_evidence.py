from __future__ import annotations

import copy

from github_inventory_test_support import InventoryTestCase, inventory


class LockEvidenceTests(InventoryTestCase):
    def test_flat_lock_closure_is_evidence_not_topology(self) -> None:
        result, _ = self.build()

        self.assertEqual(
            [(pin["package"], pin["selected_version"]) for pin in result["pins"]],
            [
                ("acme/lib-a", "1.0.0"),
                ("acme/lib-b", "1.0.0"),
                ("acme/shared", "2.0.0"),
            ],
        )
        self.assertTrue(all(pin["topological"] is False for pin in result["pins"]))
        self.assertFalse(any(edge["kind"] == "zed-lock-pin" for edge in result["edges"]))

        root = "zpkg-package:acme/app"
        shared = "zpkg-package:acme/shared"
        self.assertFalse(
            any(
                edge["source"] == root
                and edge["target"] == shared
                and edge["kind"].startswith("zed-")
                for edge in result["edges"]
            ),
            "the transitive shared pin must not become a fabricated root -> shared edge",
        )

        direct_edges = {
            edge["target"]: edge
            for edge in result["edges"]
            if edge["source"] == root and edge["kind"] == "zed-declared"
        }
        self.assertEqual(direct_edges["zpkg-package:acme/lib-a"]["selected_version"], "1.0.0")
        self.assertEqual(direct_edges["zpkg-package:acme/lib-b"]["selected_version"], "1.0.0")
        self.assertEqual(
            direct_edges["zpkg-package:acme/lib-a"]["artifact_sha256"],
            "a" * 64,
        )
        self.assertEqual(
            direct_edges["zpkg-package:acme/lib-b"]["artifact_sha256"],
            "b" * 64,
        )
        self.assertTrue(direct_edges["zpkg-package:acme/lib-a"]["selection_provenance"])

        node_ids = {node["id"] for node in result["nodes"]}
        self.assertFalse(any("@1.0.0" in node_id or "@2.0.0" in node_id for node_id in node_ids))
        analyzed_nodes = {
            node_id
            for component in result["strongly_connected_components"]
            for node_id in component["nodes"]
        }
        self.assertEqual(analyzed_nodes, node_ids)

    def test_renderers_mark_pins_as_disconnected_evidence(self) -> None:
        result, _ = self.build()
        dot = inventory.render_dot(result)
        mermaid = inventory.render_mermaid(result)

        self.assertEqual(dot.count("lock pin (evidence only)"), 3)
        self.assertIn('tooltip="non-topological lock evidence"', dot)
        self.assertNotIn('label="zed-lock-pin', dot)
        self.assertEqual(mermaid.count("lock pin (evidence only)"), 3)
        self.assertIn("classDef lockEvidence stroke-dasharray: 5 5", mermaid)
        self.assertNotIn('-->|"zed-lock-pin', mermaid)

    def test_optional_vcs_commit_is_preserved_as_pin_evidence(self) -> None:
        document = copy.deepcopy(self.fixture)
        lock = document["repositories"]["acme/app"]["files"][".zpkg.lock"]
        needle = (
            'name = "shared"\n'
            'version = "2.0.0"\n'
            f'sha256 = "{"c" * 64}"\n'
        )
        document["repositories"]["acme/app"]["files"][".zpkg.lock"] = lock.replace(
            needle,
            needle + f'vcs_commit = "{"4" * 40}"\n',
        )

        result, _ = self.build(document)
        shared_pin = next(pin for pin in result["pins"] if pin["package"] == "acme/shared")
        self.assertEqual(shared_pin["vcs_commit"], "4" * 40)
        self.assertFalse(
            any(
                edge["source"] == "zpkg-package:acme/app"
                and edge["target"] == "zpkg-package:acme/shared"
                for edge in result["edges"]
            )
        )

    def test_declared_dependency_without_lock_pin_is_explicit_contradiction(self) -> None:
        document = copy.deepcopy(self.fixture)
        lock = document["repositories"]["acme/app"]["files"][".zpkg.lock"]
        lib_b_block = f'''\n[[package]]
org = "acme"
name = "lib-b"
version = "1.0.0"
sha256 = "{"b" * 64}"
'''
        self.assertIn(lib_b_block, lock)
        document["repositories"]["acme/app"]["files"][".zpkg.lock"] = lock.replace(
            lib_b_block,
            "",
        )

        result, _ = self.build(document)
        contradiction = next(
            item
            for item in result["contradictions"]
            if item["code"] == "declared-dependency-missing-lock-pin"
        )
        self.assertEqual(contradiction["source"], "zpkg-package:acme/app")
        self.assertEqual(contradiction["target"], "zpkg-package:acme/lib-b")
        self.assertEqual(contradiction["requirement"], "^1.0")
        self.assertTrue(contradiction["declared_provenance"])
        self.assertTrue(contradiction["lock_provenance"])
        self.assertFalse(any(pin["package"] == "acme/lib-b" for pin in result["pins"]))
        lib_b_edge = next(
            edge
            for edge in result["edges"]
            if edge["source"] == "zpkg-package:acme/app"
            and edge["target"] == "zpkg-package:acme/lib-b"
            and edge["kind"] == "zed-declared"
        )
        self.assertNotIn("selected_version", lib_b_edge)

    def test_invalid_artifact_digest_is_explicit_and_not_annotated(self) -> None:
        document = copy.deepcopy(self.fixture)
        lock = document["repositories"]["acme/app"]["files"][".zpkg.lock"]
        document["repositories"]["acme/app"]["files"][".zpkg.lock"] = lock.replace(
            f'sha256 = "{"a" * 64}"',
            'sha256 = "not-a-sha256"',
        )

        result, _ = self.build(document)
        self.assertEqual(result["completeness"]["inventory"], "partial")
        self.assertTrue(
            any(failure["code"] == "invalid_lock_artifact_sha256" for failure in result["failures"])
        )
        self.assertTrue(
            any(
                item["code"] == "declared-dependency-missing-lock-pin"
                and item["target"] == "zpkg-package:acme/lib-a"
                for item in result["contradictions"]
            )
        )
        self.assertFalse(any(pin["package"] == "acme/lib-a" for pin in result["pins"]))
        lib_a_edge = next(
            edge
            for edge in result["edges"]
            if edge["source"] == "zpkg-package:acme/app"
            and edge["target"] == "zpkg-package:acme/lib-a"
            and edge["kind"] == "zed-declared"
        )
        self.assertNotIn("selected_version", lib_a_edge)

    def test_duplicate_identical_lock_entries_are_deduplicated(self) -> None:
        document = copy.deepcopy(self.fixture)
        document["repositories"]["acme/app"]["files"][".zpkg.lock"] += f'''\n[[package]]
org = "acme"
name = "lib-a"
version = "1.0.0"
sha256 = "{"a" * 64}"
'''
        result, _ = self.build(document)
        self.assertEqual(
            sum(1 for pin in result["pins"] if pin["package"] == "acme/lib-a"),
            1,
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
