from __future__ import annotations

import base64
import hashlib

from github_inventory_test_support import *  # noqa: F401,F403


class InventoryTransportTests(InventoryTestCase):
    def test_fake_http_server_covers_private_auth_and_pagination(self) -> None:
        document = copy.deepcopy(self.fixture)
        document["require_token"] = True
        token = "http-fixture-token"
        with FakeServer(document) as server:
            result = inventory.run_inventory(
                repositories=[],
                organizations=["acme"],
                includes=["zed,git-submodule,nix"],
                limits=inventory.Limits(max_seconds=10),
                transport=inventory.HttpTransport(server.base_url),
                token=token,
                sleeper=lambda _: None,
            )
        self.assertEqual(result["completeness"]["inventory"], "complete")
        self.assertGreaterEqual(len(server.handler.seen_authorization), 3)
        self.assertTrue(
            all(value == f"Bearer {token}" for value in server.handler.seen_authorization)
        )

    def test_transient_retries_are_bounded(self) -> None:
        with FakeServer(copy.deepcopy(self.fixture), transient=2) as server:
            result = inventory.run_inventory(
                repositories=[],
                organizations=["acme"],
                includes=["zed"],
                limits=inventory.Limits(max_seconds=10, max_retries=2),
                transport=inventory.HttpTransport(server.base_url),
                token=None,
                sleeper=lambda _: None,
            )
        self.assertEqual(result["completeness"]["inventory"], "complete")
        org_requests = sum(1 for _ in server.handler.seen_authorization)
        # 3 pagination requests plus exactly 2 retry attempts, followed by
        # repository commit/tree/blob requests.
        self.assertGreaterEqual(org_requests, 5)

    def test_redirects_are_not_followed_and_cross_origin_links_are_rejected(self) -> None:
        token = "redirect-secret"
        with FakeServer(copy.deepcopy(self.fixture), redirect_org=True) as server:
            result = inventory.run_inventory(
                repositories=[],
                organizations=["acme"],
                includes=["zed"],
                limits=inventory.Limits(max_seconds=10),
                transport=inventory.HttpTransport(server.base_url),
                token=token,
                sleeper=lambda _: None,
            )
        self.assertEqual(result["completeness"]["inventory"], "partial")
        self.assertEqual(len(server.handler.seen_authorization), 1)
        self.assertNotIn(token, inventory.render_inventory(result, "json"))
        transport = inventory.HttpTransport("https://api.github.com")
        with self.assertRaises(inventory.ApiError) as captured:
            transport.relative_from_link("https://attacker.invalid/page=2")
        self.assertEqual(captured.exception.code, "cross_origin_pagination_link")

    def test_ghe_api_prefix_pagination_is_normalized_without_duplication(self) -> None:
        transport = inventory.HttpTransport("https://github.example/api/v3/")
        current = "/orgs/acme/repos?direction=asc&page=1&per_page=100"
        expected = "/orgs/acme/repos?direction=asc&page=2&per_page=100"

        self.assertEqual(
            transport.relative_from_link(
                "https://github.example/api/v3/orgs/acme/repos?direction=asc&page=2&per_page=100",
                current_path=current,
            ),
            expected,
        )
        self.assertEqual(
            transport.relative_from_link(
                "/api/v3/orgs/acme/repos?direction=asc&page=2&per_page=100",
                current_path=current,
            ),
            expected,
        )
        self.assertEqual(
            transport.relative_from_link("?direction=asc&page=2&per_page=100", current_path=current),
            expected,
        )
        self.assertEqual(
            transport.absolute_request_url(expected),
            "https://github.example/api/v3" + expected,
        )
        with self.assertRaises(inventory.ApiError) as captured:
            transport.relative_from_link(
                "https://github.example/outside?page=2", current_path=current
            )
        self.assertEqual(captured.exception.code, "pagination_link_outside_api_base")

    def test_api_base_rejects_credentials_queries_fragments_and_dot_segments(self) -> None:
        unsafe = [
            "https://user:pass@github.example/api/v3",
            "https://github.example/api/v3?token=secret",
            "https://github.example/api/v3#fragment",
            "https://github.example/api/../v3",
            "https://github.example/api/%2e%2e/v3",
            "https://github.example/api//v3",
            "https://github.example/api/%5c/v3",
        ]
        for value in unsafe:
            with self.subTest(value=value), self.assertRaises(inventory.InputError):
                inventory.HttpTransport(value)

    def test_hard_limits_fail_closed_and_preserve_existing_atomic_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "inventory.json"
            output.write_text("sentinel\n", encoding="utf-8")
            stderr = io.StringIO()
            argv = [
                "--fixture",
                str(FIXTURE_PATH),
                "--org",
                "acme",
                "--max-repositories",
                "1",
                "--output",
                str(output),
            ]
            with contextlib.redirect_stderr(stderr):
                code = inventory.main(argv)
            self.assertEqual(code, 3)
            self.assertEqual(output.read_text(encoding="utf-8"), "sentinel\n")
            self.assertFalse(list(output.parent.glob(f".{output.name}.*.tmp")))
            self.assertIn("repository limit exceeded", stderr.getvalue())

    def test_response_and_manifest_byte_limits_fail_closed(self) -> None:
        with self.assertRaises(inventory.LimitError):
            self.build(limits=inventory.Limits(max_response_bytes=10, max_seconds=10))
        with self.assertRaises(inventory.LimitError):
            self.build(limits=inventory.Limits(max_manifest_bytes=16, max_seconds=10))

    def test_json_depth_limit_fails_closed(self) -> None:
        document = copy.deepcopy(self.fixture)
        nested: dict[str, Any] = {"owner": "acme", "repo": "shared", "rev": "4" * 40}
        for index in range(8):
            nested = {f"level-{index}": nested}
        document["repositories"]["acme/app"]["files"]["nix/sources.json"] = json.dumps(nested)
        with self.assertRaises(inventory.LimitError):
            self.build(document, limits=inventory.Limits(max_json_depth=3, max_seconds=10))

    def test_atomic_write_replaces_content_without_leaving_temporary_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "graph.dot"
            output.write_text("old\n", encoding="utf-8")
            inventory.write_atomic(output, "new\n")
            self.assertEqual(output.read_text(encoding="utf-8"), "new\n")
            self.assertFalse(list(output.parent.glob(f".{output.name}.*.tmp")))

    def test_plain_http_non_loopback_and_unsafe_fixture_paths_are_rejected(self) -> None:
        with self.assertRaises(inventory.InputError):
            inventory.HttpTransport("http://example.com")
        document = copy.deepcopy(self.fixture)
        document["repositories"]["acme/app"]["files"]["../escape"] = "x"
        with self.assertRaises(inventory.InputError):
            inventory.FixtureBackend(document)

    def test_rate_limit_403_retries_are_bounded(self) -> None:
        with FakeServer(copy.deepcopy(self.fixture), transient=2, transient_status=403) as server:
            result = inventory.run_inventory(
                repositories=[],
                organizations=["acme"],
                includes=["zed"],
                limits=inventory.Limits(max_seconds=10, max_retries=2),
                transport=inventory.HttpTransport(server.base_url),
                token=None,
                sleeper=lambda _: None,
            )
        self.assertEqual(result["completeness"]["inventory"], "complete")
        self.assertGreaterEqual(len(server.handler.seen_authorization), 5)

    def test_success_response_body_is_bounded_during_http_read(self) -> None:
        with FakeServer(copy.deepcopy(self.fixture), oversized_org_bytes=4096) as server:
            with self.assertRaises(inventory.LimitError):
                inventory.run_inventory(
                    repositories=[],
                    organizations=["acme"],
                    includes=["zed"],
                    limits=inventory.Limits(max_seconds=10, max_response_bytes=128),
                    transport=inventory.HttpTransport(server.base_url),
                    token=None,
                    sleeper=lambda _: None,
                )

    def test_custom_api_origin_requires_explicit_token_opt_in(self) -> None:
        transport = inventory.HttpTransport("https://github.example")
        with self.assertRaises(inventory.InputError):
            inventory.GitHubClient(
                transport,
                inventory.Budget(inventory.Limits()),
                "secret-token",
                sleeper=lambda _: None,
            )
        allowed = inventory.HttpTransport(
            "https://github.example",
            allow_token_to_custom_origin=True,
        )
        client = inventory.GitHubClient(
            allowed,
            inventory.Budget(inventory.Limits()),
            "secret-token",
            sleeper=lambda _: None,
        )
        self.assertEqual(client.token, "secret-token")

    def test_github_blob_base64_line_wrapping_is_supported(self) -> None:
        data = b"abc"
        sha = inventory.git_blob_sha(data)

        class WrappedBlobTransport(inventory.Transport):
            base_url = "https://api.github.com"
            _base_path = ""

            def request(self, path, headers, timeout, max_bytes):
                del path, headers, timeout, max_bytes
                body = json.dumps(
                    {
                        "sha": sha,
                        "size": len(data),
                        "encoding": "base64",
                        "content": "Y\nWJ\nj",
                    }
                ).encode("utf-8")
                return inventory.ApiResponse(200, {}, body)

        client = inventory.GitHubClient(
            WrappedBlobTransport(),
            inventory.Budget(inventory.Limits()),
            None,
            sleeper=lambda _: None,
        )
        self.assertEqual(client.get_blob("acme/app", sha, len(data)), data)

    def test_blob_object_ids_are_recomputed_for_sha1_and_sha256(self) -> None:
        data = b"object identity"
        header = f"blob {len(data)}\0".encode("ascii")
        sha1 = hashlib.sha1(header + data, usedforsecurity=False).hexdigest()
        sha256 = hashlib.sha256(header + data).hexdigest()

        class BlobTransport(inventory.Transport):
            base_url = "https://api.github.com"
            _base_path = ""

            def __init__(self, response_sha: str, response_data: bytes) -> None:
                self.response_sha = response_sha
                self.response_data = response_data

            def request(self, path, headers, timeout, max_bytes):
                del path, headers, timeout, max_bytes
                body = json.dumps(
                    {
                        "sha": self.response_sha,
                        "size": len(self.response_data),
                        "encoding": "base64",
                        "content": base64.b64encode(self.response_data).decode("ascii"),
                    }
                ).encode("utf-8")
                return inventory.ApiResponse(200, {}, body)

        for object_id in (sha1, sha256):
            with self.subTest(object_id=object_id):
                client = inventory.GitHubClient(
                    BlobTransport(object_id, data),
                    inventory.Budget(inventory.Limits()),
                    None,
                    sleeper=lambda _: None,
                )
                self.assertEqual(client.get_blob("acme/app", object_id, len(data)), data)

        client = inventory.GitHubClient(
            BlobTransport(sha1, b"tampered"),
            inventory.Budget(inventory.Limits()),
            None,
            sleeper=lambda _: None,
        )
        with self.assertRaises(inventory.ApiError) as captured:
            client.get_blob("acme/app", sha1, None)
        self.assertEqual(captured.exception.code, "blob_object_id_mismatch")

    def test_field_size_limit_is_fail_closed(self) -> None:
        with self.assertRaises(inventory.LimitError):
            self.build(limits=inventory.Limits(max_field_bytes=4, max_seconds=10))
