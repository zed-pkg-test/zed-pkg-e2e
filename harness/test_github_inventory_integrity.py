from __future__ import annotations

from github_inventory_test_support import *  # noqa: F401,F403


class InventoryIntegrityTests(InventoryTestCase):
    def test_build_dependency_aliases_emit_typed_edges_and_receive_lock_pins(self) -> None:
        document = copy.deepcopy(self.fixture)
        document["repositories"]["acme/app"]["files"][".zpkg.toml"] += '''

[build-dependencies]
"acme/lib-a" = "^1.0"

[build_dependencies]
"acme/shared" = "^2.0"
'''
        result, _ = self.build(document)
        build_edges = {
            edge["target"]: edge
            for edge in result["edges"]
            if edge["source"] == "zpkg-package:acme/app"
            and edge["kind"] == "zed-build-declared"
        }
        self.assertEqual(
            set(build_edges),
            {"zpkg-package:acme/lib-a", "zpkg-package:acme/shared"},
        )
        self.assertEqual(build_edges["zpkg-package:acme/lib-a"]["selected_version"], "1.0.0")
        self.assertEqual(build_edges["zpkg-package:acme/shared"]["selected_version"], "2.0.0")
        self.assertEqual(
            build_edges["zpkg-package:acme/lib-a"]["artifact_sha256"], "a" * 64
        )
        self.assertEqual(
            build_edges["zpkg-package:acme/shared"]["artifact_sha256"], "c" * 64
        )

    def test_optional_lock_identity_conflicts_sort_stably_without_type_errors(self) -> None:
        document = copy.deepcopy(self.fixture)
        document["repositories"]["acme/app"]["files"][".zpkg.lock"] += '''

[[package]]
org = "acme"
name = "lib-a"
version = "1.0.0"

[[package]]
org = "acme"
name = "lib-a"
version = "1.0.0"
sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
vcs_commit = "2222222222222222222222222222222222222222"
'''
        first, _ = self.build(document)
        second_document = copy.deepcopy(document)
        second_document["organizations"]["acme"].reverse()
        second, _ = self.build(second_document)
        self.assertEqual(first["contradictions"], second["contradictions"])
        conflict = next(
            item
            for item in first["contradictions"]
            if item["code"] == "conflicting-zed-lock-pins"
            and item["target"] == "zpkg-package:acme/lib-a"
        )
        self.assertEqual(
            conflict["selections"],
            [
                {"version": "1.0.0"},
                {"version": "1.0.0", "artifact_sha256": "a" * 64},
                {
                    "version": "1.0.0",
                    "artifact_sha256": "a" * 64,
                    "vcs_commit": "2" * 40,
                },
            ],
        )

    def test_exact_gitlinks_require_matching_type_mode_and_object_id(self) -> None:
        backend = inventory.FixtureBackend(copy.deepcopy(self.fixture))
        client = inventory.GitHubClient(
            inventory.FixtureTransport(backend),
            inventory.Budget(inventory.Limits()),
            None,
            sleeper=lambda _: None,
        )
        builder = inventory.InventoryBuilder(
            client,
            inventory.Limits(),
            ["acme/app"],
            [],
            ["git-submodule"],
            None,
        )
        valid = {
            "vendor/shared": {
                "path": "vendor/shared",
                "type": "commit",
                "mode": "160000",
                "sha": "4" * 40,
            }
        }
        self.assertEqual(builder._exact_gitlinks(valid), {"vendor/shared": "4" * 40})

        invalid_entries = [
            {"path": "vendor/shared", "type": "commit", "mode": "040000", "sha": "4" * 40},
            {"path": "vendor/shared", "type": "blob", "mode": "160000", "sha": "4" * 40},
            {"path": "vendor/shared", "type": "commit", "mode": "160000", "sha": "not-an-oid"},
        ]
        for entry in invalid_entries:
            with self.subTest(entry=entry), self.assertRaises(inventory.ParseFailure):
                builder._exact_gitlinks({"vendor/shared": entry})

        with self.assertRaises(inventory.ParseFailure):
            builder._tree_entries(
                [
                    {"path": "vendor/shared", "type": "commit", "mode": "160000", "sha": "4" * 40},
                    {"path": "vendor/shared", "type": "blob", "mode": "100644", "sha": "5" * 40},
                ]
            )
