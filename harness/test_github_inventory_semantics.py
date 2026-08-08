from __future__ import annotations

from github_inventory_test_support import *  # noqa: F401,F403


class InventorySemanticsTests(InventoryTestCase):
    def test_golden_json_dot_and_mermaid_are_byte_identical(self) -> None:
        result, _ = self.build()
        for output_format, filename in (
            ("json", "inventory.json"),
            ("dot", "inventory.dot"),
            ("mermaid", "inventory.mmd"),
        ):
            actual = inventory.render_inventory(result, output_format)
            expected = (GOLDEN / filename).read_text(encoding="utf-8")
            self.assertEqual(actual, expected, output_format)

    def test_org_pagination_is_complete_deduplicated_and_archived_is_explicit(self) -> None:
        result, transport = self.build(repositories=[])
        pages = [path for path in transport.requests if path.startswith("/orgs/acme/repos?")]
        self.assertEqual(len(pages), 3)
        records = {item["full_name"]: item for item in result["repositories"]}
        self.assertEqual(len(records), 7)
        self.assertTrue(records["acme/archived"]["archived"])
        self.assertEqual(records["acme/archived"]["status"], "scanned")
        self.assertEqual(
            records["acme/archived"]["missing_manifests"],
            [".gitmodules", ".zpkg.lock", ".zpkg.toml", "flake.lock", "flake.nix", "nix/sources.json", "npins/sources.json"],
        )
        self.assertTrue(records["acme/app"]["private"])
        self.assertEqual(result["completeness"]["inventory"], "complete")
        self.assertEqual(result["completeness"]["resolution"], "not-claimed")

    def test_cycle_scc_and_dependency_first_waves_are_deterministic(self) -> None:
        result, _ = self.build()
        self.assertIn(
            ["zpkg-package:acme/cycle-a", "zpkg-package:acme/cycle-b"],
            result["cycles"],
        )
        component_for: dict[str, str] = {}
        for component in result["strongly_connected_components"]:
            for node in component["nodes"]:
                component_for[node] = component["id"]
        wave_for = {
            component: index
            for index, wave in enumerate(result["topological_waves"])
            for component in wave
        }
        shared = component_for["zpkg-package:acme/shared"]
        lib_a = component_for["zpkg-package:acme/lib-a"]
        lib_b = component_for["zpkg-package:acme/lib-b"]
        self.assertLess(wave_for[shared], wave_for[lib_a])
        self.assertLess(wave_for[shared], wave_for[lib_b])
        incoming = {
            edge["source"]
            for edge in result["edges"]
            if edge["target"] == "zpkg-package:acme/shared" and edge["kind"] == "zed-declared"
        }
        self.assertEqual(
            incoming,
            {"zpkg-package:acme/lib-a", "zpkg-package:acme/lib-b"},
        )

    def test_hostile_labels_are_escaped_in_dot_and_mermaid(self) -> None:
        result, _ = self.build()
        dot = inventory.render_dot(result)
        mermaid = inventory.render_mermaid(result)
        self.assertIn(r'^1.0\" ]\nmalicious', dot)
        self.assertNotIn("\nmalicious\"]", dot)
        self.assertIn("^1.0&quot; &#93;&#10;malicious", mermaid)
        self.assertNotIn('^1.0" ]\nmalicious', mermaid)
        for node in result["nodes"]:
            self.assertRegex(inventory.render_node_id(node["id"]), r"^n_[0-9a-f]{20}$")

    def test_duplicate_edges_merge_provenance_and_sort_stably(self) -> None:
        document = copy.deepcopy(self.fixture)
        document["repositories"]["acme/lib-a"]["files"]["flake.nix"] = (
            '{ inputs.shared.url = "github:acme/shared"; }\n'
        )
        first, _ = self.build(document)
        reversed_document = copy.deepcopy(document)
        reversed_document["organizations"]["acme"].reverse()
        reversed_document["repositories"] = dict(
            reversed(list(reversed_document["repositories"].items()))
        )
        second, _ = self.build(reversed_document)
        self.assertEqual(first, second)
        self.assertEqual(
            inventory.render_inventory(first, "json"),
            inventory.render_inventory(second, "json"),
        )

    def test_conflicting_lock_pins_are_diagnostic_not_silently_collapsed(self) -> None:
        document = copy.deepcopy(self.fixture)
        document["repositories"]["acme/app"]["files"][".zpkg.lock"] += '''
[[package]]
org = "acme"
name = "lib-a"
version = "2.0.0"
sha256 = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
'''
        result, _ = self.build(document)
        conflicts = [
            item for item in result["contradictions"] if item["code"] == "conflicting-zed-lock-pins"
        ]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(
            [selection["version"] for selection in conflicts[0]["selections"]],
            ["1.0.0", "2.0.0"],
        )

    def test_tree_truncation_is_explicit_and_never_claims_complete(self) -> None:
        document = copy.deepcopy(self.fixture)
        document["repositories"]["acme/lib-b"]["tree_truncated"] = True
        result, _ = self.build(document)
        self.assertEqual(result["completeness"]["inventory"], "partial")
        failure = next(item for item in result["failures"] if item["repository"] == "acme/lib-b")
        self.assertEqual(failure["code"], "github_tree_truncated")
        record = next(item for item in result["repositories"] if item["full_name"] == "acme/lib-b")
        self.assertEqual(record["status"], "failed")

    def test_token_is_environment_only_and_redacted_from_failures(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as captured:
                inventory.build_parser().parse_args(["--org", "acme", "--token", "secret"])
        self.assertEqual(captured.exception.code, 2)

        token = "fixture-super-secret-token"
        document = copy.deepcopy(self.fixture)
        document["require_token"] = True
        document["repositories"]["acme/app"]["failures"] = {
            "tree": {"status": 500, "body": {"message": token}}
        }
        result, transport = self.build(document, token=token)
        self.assertEqual(result["completeness"]["inventory"], "partial")
        serialized = inventory.render_inventory(result, "json")
        self.assertNotIn(token, serialized)
        self.assertTrue(transport.requests)
        self.assertNotIn(token, "\n".join(transport.requests))

    def test_iterative_scc_handles_ten_thousand_node_chain(self) -> None:
        count = 10_000
        nodes = {
            f"node-{index:05d}": {"id": f"node-{index:05d}"}
            for index in range(count)
        }
        edges = [
            {
                "source": f"node-{index:05d}",
                "target": f"node-{index + 1:05d}",
            }
            for index in range(count - 1)
        ]
        analysis = inventory.analyze_graph(nodes, edges)
        self.assertEqual(len(analysis["components"]), count)
        self.assertEqual(len(analysis["waves"]), count)
        self.assertFalse(analysis["cycles"])

    def test_no_token_or_authorization_material_is_accepted_in_output_paths(self) -> None:
        result, _ = self.build(token="secret-token")
        serialized = inventory.render_inventory(result, "json")
        self.assertNotIn("secret-token", serialized)
        self.assertNotIn("Authorization", serialized)
        self.assertNotIn("Bearer", serialized)
