from __future__ import annotations

import copy
import contextlib
import io
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FIXTURE_PATH = ROOT / "fixtures" / "github-dependency-inventory" / "fixture.json"
GOLDEN = ROOT / "fixtures" / "github-dependency-inventory" / "golden"
sys.path.insert(0, str(HERE))

import github_dependency_inventory as inventory  # noqa: E402


class QuietHandler(BaseHTTPRequestHandler):
    backend: inventory.FixtureBackend
    seen_authorization: list[str | None]
    transient_remaining: int = 0
    transient_status: int = 503
    redirect_org: bool = False
    oversized_org_bytes: int = 0

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        type(self).seen_authorization.append(self.headers.get("Authorization"))
        if type(self).redirect_org and self.path.startswith("/orgs/"):
            self.send_response(302)
            self.send_header("Location", "https://attacker.invalid/steal")
            self.end_headers()
            return
        if type(self).oversized_org_bytes and self.path.startswith("/orgs/"):
            body = b"x" * type(self).oversized_org_bytes
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if type(self).transient_remaining > 0 and self.path.startswith("/orgs/"):
            type(self).transient_remaining -= 1
            self.send_response(type(self).transient_status)
            self.send_header("Retry-After", "0")
            if type(self).transient_status == 403:
                self.send_header("X-RateLimit-Remaining", "0")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"message":"try later"}')
            return
        response = type(self).backend.handle(self.path, self.headers.get("Authorization"))
        self.send_response(response.status)
        for key, value in response.headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(response.body)

    def log_message(self, fmt: str, *args: object) -> None:
        del fmt, args


class FakeServer:
    def __init__(
        self,
        document: dict[str, Any],
        *,
        transient: int = 0,
        transient_status: int = 503,
        redirect_org: bool = False,
        oversized_org_bytes: int = 0,
    ) -> None:
        handler = type("BoundQuietHandler", (QuietHandler,), {})
        handler.seen_authorization = []
        handler.transient_remaining = transient
        handler.transient_status = transient_status
        handler.redirect_org = redirect_org
        handler.oversized_org_bytes = oversized_org_bytes
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"
        handler.backend = inventory.FixtureBackend(document, base_url=self.base_url)
        self.handler = handler
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "FakeServer":
        self.thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class InventoryTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def build(
        self,
        document: dict[str, Any] | None = None,
        *,
        repositories: list[str] | None = None,
        organizations: list[str] | None = None,
        includes: list[str] | None = None,
        limits: inventory.Limits | None = None,
        token: str | None = None,
    ) -> tuple[dict[str, Any], inventory.FixtureTransport]:
        backend = inventory.FixtureBackend(copy.deepcopy(document or self.fixture))
        transport = inventory.FixtureTransport(backend)
        result = inventory.run_inventory(
            repositories=repositories or ["ACME/App", "acme/app"],
            organizations=organizations or ["ACME", "acme"],
            includes=includes or ["nix,zed", "git-submodule", "zed"],
            limits=limits or inventory.Limits(),
            transport=transport,
            token=token,
            sleeper=lambda _: None,
        )
        return result, transport
