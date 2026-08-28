#!/usr/bin/env python3
"""Credential-free HTTP transport tests for the static registry checker."""
from __future__ import annotations

import contextlib
import http.server
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILDER = ROOT / "build_static_registry.py"
CHECKER = ROOT / "check_static_registry.py"
EXPECTED_USER_AGENT = "zpkg-static-registry-check/0"


class RecordingHandler(http.server.SimpleHTTPRequestHandler):
    root: Path
    user_agents: list[str]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(self.root), **kwargs)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.user_agents.append(self.headers.get("User-Agent", ""))
        super().do_GET()

    def log_message(self, format: str, *args) -> None:
        del format, args


@contextlib.contextmanager
def static_server(root: Path):
    user_agents: list[str] = []

    class Handler(RecordingHandler):
        pass

    Handler.root = root
    Handler.user_agents = user_agents
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", user_agents
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()


class StaticRegistryHttpTests(unittest.TestCase):
    def build_tree(self, root: Path) -> Path:
        tree = root / "tree"
        environment = os.environ.copy()
        environment["SOURCE_DATE_EPOCH"] = "0"
        result = subprocess.run(
            [
                sys.executable,
                str(BUILDER),
                "--fixtures",
                str(ROOT / "fixtures"),
                "--out",
                str(tree),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return tree

    def test_plain_http_server_passes_full_conformance_with_identifying_user_agent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tree = self.build_tree(Path(directory))
            with static_server(tree) as (base, user_agents):
                result = subprocess.run(
                    [sys.executable, str(CHECKER), "--base", base],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("== PASS", result.stdout)
            self.assertGreaterEqual(len(user_agents), 1)
            self.assertEqual(set(user_agents), {EXPECTED_USER_AGENT})

    def test_http_checker_fails_cleanly_when_checkpointed_object_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tree = self.build_tree(Path(directory))
            missing = tree / "pkgs/zpkg-e2e/hello-zed/1.0.0.tar.zst"
            missing.unlink()
            with static_server(tree) as (base, user_agents):
                result = subprocess.run(
                    [sys.executable, str(CHECKER), "--base", base],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )

            self.assertEqual(result.returncode, 1)
            self.assertIn("cannot read checkpointed object", result.stdout)
            self.assertNotIn("Traceback", result.stdout + result.stderr)
            self.assertEqual(set(user_agents), {EXPECTED_USER_AGENT})

    def test_http_checker_rejects_invalid_discovery_json_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tree = self.build_tree(Path(directory))
            discovery = tree / ".well-known/zpkg-registry.json"
            discovery.write_text("not-json\n", encoding="utf-8")
            with static_server(tree) as (base, user_agents):
                result = subprocess.run(
                    [sys.executable, str(CHECKER), "--base", base],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )

            self.assertEqual(result.returncode, 1)
            self.assertIn("cannot read discovery document", result.stdout)
            self.assertNotIn("Traceback", result.stdout + result.stderr)
            self.assertEqual(set(user_agents), {EXPECTED_USER_AGENT})


if __name__ == "__main__":
    unittest.main()
