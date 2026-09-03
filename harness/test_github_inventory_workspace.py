from __future__ import annotations

from github_inventory_test_support import *  # noqa: F401,F403


ROOT_MANIFEST = '''
[package]
name = "workspace-monorepo"
version = "0.1.0"

[workspace]
members = ["packages/core", "packages/utils", "apps/cli"]

[targets.repository]
dir = "."
'''

CORE_MANIFEST = '''
[package]
name = "workspace-core"
version = "0.1.0"

[dependencies]
lodash = "^4.17.21"
'''

UTILS_MANIFEST = '''
[package]
name = "workspace-utils"
version = "0.1.0"

[dependencies]
workspace-core = { path = "../core" }
'''

CLI_MANIFEST = '''
[package]
name = "workspace-cli"
version = "0.1.0"

[dependencies]
workspace-core = { path = "../../packages/core" }
workspace-utils = { path = "../../packages/utils" }
'''


class WorkspaceInventoryTests(InventoryTestCase):
    def workspace_fixture(self) -> dict[str, object]:
        document = copy.deepcopy(self.fixture)
        document["organizations"] = {"acme": ["acme/app"]}
        repository = document["repositories"]["acme/app"]
        repository["files"] = {
            ".zpkg.toml": ROOT_MANIFEST,
            "packages/core/.zpkg.toml": CORE_MANIFEST,
            "packages/utils/.zpkg.toml": UTILS_MANIFEST,
            "apps/cli/.zpkg.toml": CLI_MANIFEST,
        }
        repository["gitlinks"] = {}
        document["repositories"] = {"acme/app": repository}
        return document

    def build_workspace(self, document: dict[str, object] | None = None, *, limits=None):
        return self.build(
            document or self.workspace_fixture(),
            repositories=["acme/app"],
            organizations=["acme"],
            includes=["zed"],
            limits=limits,
        )

    @staticmethod
    def workspace_render_projection(result: dict[str, object]) -> dict[str, object]:
        nodes = [
            {
                "id": node["id"],
                "kind": node["kind"],
                "label": node["label"],
            }
            for node in result["nodes"]
            if node.get("repository") == "acme/app"
            and node.get("zed_manifest_path")
        ]
        nodes.sort(key=lambda node: node["id"])
        node_ids = {node["id"] for node in nodes}
        edges = [
            {
                key: edge[key]
                for key in ("source", "target", "kind", "source_path", "input_name")
                if key in edge
            }
            for edge in result["edges"]
            if edge["source"] in node_ids and edge["target"] in node_ids
        ]
        edges.sort(
            key=lambda edge: (
                edge["source"],
                edge["target"],
                edge["kind"],
                edge.get("source_path", ""),
                edge.get("input_name", ""),
            )
        )
        return {"nodes": nodes, "edges": edges, "pins": []}

    def test_workspace_root_members_and_local_dependencies_are_complete(self) -> None:
        result, transport = self.build_workspace()
        record = result["repositories"][0]
        self.assertEqual(record["status"], "scanned")
        self.assertEqual(
            [item["path"] for item in record["manifests"]],
            [
                ".zpkg.toml",
                "apps/cli/.zpkg.toml",
                "packages/core/.zpkg.toml",
                "packages/utils/.zpkg.toml",
            ],
        )
        self.assertEqual(len(record["zed_workspace_members"]), 3)
        node_ids = {node["id"] for node in result["nodes"]}

        def package_id(name: str) -> str:
            prefix = f"zpkg-package:acme/{name}"
            matches = sorted(
                node_id
                for node_id in node_ids
                if node_id == prefix or node_id.startswith(prefix + "@")
            )
            self.assertEqual(len(matches), 1, matches)
            return matches[0]

        root_id = package_id("workspace-monorepo")
        core_id = package_id("workspace-core")
        utils_id = package_id("workspace-utils")
        cli_id = package_id("workspace-cli")
        self.assertEqual(result["package_roots"]["acme/app"], root_id)

        memberships = [
            edge for edge in result["edges"] if edge["kind"] == "zed-workspace-member"
        ]
        self.assertEqual(len(memberships), 3)
        self.assertEqual(
            {edge["target"] for edge in memberships},
            {core_id, utils_id, cli_id},
        )
        local_edges = {
            (edge["source"], edge["target"])
            for edge in result["edges"]
            if edge["kind"] == "zed-declared" and edge.get("source_path")
        }
        self.assertEqual(
            local_edges,
            {
                (utils_id, core_id),
                (cli_id, core_id),
                (cli_id, utils_id),
            },
        )
        manifest_blob_requests = [
            request for request in transport.requests if "/git/blobs/" in request
        ]
        self.assertEqual(len(manifest_blob_requests), 4)
        for membership in memberships:
            self.assertEqual(len(membership["provenance"]), 2)
            self.assertEqual(
                {item["repository_commit"] for item in membership["provenance"]},
                {"1111111111111111111111111111111111111111"},
            )

    def test_workspace_rendering_matches_checked_in_goldens(self) -> None:
        first, _ = self.build_workspace()
        document = self.workspace_fixture()
        files = document["repositories"]["acme/app"]["files"]
        document["repositories"]["acme/app"]["files"] = dict(
            reversed(list(files.items()))
        )
        second, _ = self.build_workspace(document)
        self.assertEqual(first, second)

        first_projection = self.workspace_render_projection(first)
        second_projection = self.workspace_render_projection(second)
        self.assertEqual(first_projection, second_projection)

        goldens = {
            "json": GOLDEN / "workspace-inventory.json",
            "dot": GOLDEN / "workspace-inventory.dot",
            "mermaid": GOLDEN / "workspace-inventory.mmd",
        }
        for output_format, golden_path in goldens.items():
            expected = golden_path.read_text(encoding="utf-8")
            self.assertEqual(
                inventory.render_inventory(first_projection, output_format),
                expected,
                f"{output_format} workspace golden drifted",
            )
            self.assertEqual(
                inventory.render_inventory(second_projection, output_format),
                expected,
                f"{output_format} workspace rendering depends on acquisition order",
            )

    def assert_workspace_failure(self, document: dict[str, object], text: str) -> None:
        result, _ = self.build_workspace(document)
        record = result["repositories"][0]
        self.assertEqual(record["status"], "failed")
        self.assertIn(text, record["failure_message"])
        self.assertEqual(result["completeness"]["inventory"], "partial")

    def test_workspace_member_path_escape_is_rejected(self) -> None:
        document = self.workspace_fixture()
        document["repositories"]["acme/app"]["files"][".zpkg.toml"] = (
            ROOT_MANIFEST.replace(
                'members = ["packages/core", "packages/utils", "apps/cli"]',
                'members = ["../outside"]',
            )
        )
        self.assert_workspace_failure(document, "unsafe workspace member path")

    def test_duplicate_workspace_member_is_rejected(self) -> None:
        document = self.workspace_fixture()
        document["repositories"]["acme/app"]["files"][".zpkg.toml"] = (
            ROOT_MANIFEST.replace(
                'members = ["packages/core", "packages/utils", "apps/cli"]',
                'members = ["packages/core", "packages/core"]',
            )
        )
        self.assert_workspace_failure(document, "duplicate workspace member")

    def test_missing_declared_member_is_rejected(self) -> None:
        document = self.workspace_fixture()
        del document["repositories"]["acme/app"]["files"]["packages/core/.zpkg.toml"]
        self.assert_workspace_failure(document, "is missing packages/core/.zpkg.toml")

    def test_ambiguous_member_identity_is_rejected(self) -> None:
        document = self.workspace_fixture()
        document["repositories"]["acme/app"]["files"]["packages/utils/.zpkg.toml"] = (
            CORE_MANIFEST
        )
        self.assert_workspace_failure(document, "ambiguous Zed package identity")

    def test_missing_local_member_dependency_is_rejected(self) -> None:
        document = self.workspace_fixture()
        document["repositories"]["acme/app"]["files"]["packages/utils/.zpkg.toml"] = (
            UTILS_MANIFEST.replace('../core', '../missing')
        )
        self.assert_workspace_failure(document, "targets missing member")

    def test_local_dependency_repo_escape_is_rejected(self) -> None:
        document = self.workspace_fixture()
        document["repositories"]["acme/app"]["files"]["packages/utils/.zpkg.toml"] = (
            UTILS_MANIFEST.replace('../core', '../../../outside')
        )
        self.assert_workspace_failure(document, "escapes repository")

    def test_workspace_depth_limit_is_fail_closed(self) -> None:
        document = self.workspace_fixture()
        limits = inventory.Limits(max_json_depth=0)
        result, _ = self.build_workspace(document, limits=limits)
        record = result["repositories"][0]
        self.assertEqual(record["status"], "failed")
        self.assertIn("workspace nesting exceeded", record["failure_message"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
