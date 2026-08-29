#!/usr/bin/env python3
"""Cross-platform production verifier for ``zed graph github``.

No third-party packages are used. The verifier runs the candidate against a
bounded in-process GitHub fake, checks deterministic JSON/DOT/Mermaid bytes,
exercises pagination/redirect/truncated-tree failures, and optionally performs
a redacted Linux-only live smoke against ``zed-pkg-test``.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

MAX_CAPTURE_BYTES = 1_048_576
MAX_EVIDENCE_BYTES = 262_144
DEFAULT_TIMEOUT_SECONDS = 45.0
LIVE_TIMEOUT_SECONDS = 180.0
TOKEN_SENTINEL = "zpkg-test-token-7d8e1af1-redaction-sentinel"
SCHEMA = "zpkg/github-dependency-inventory/v1"
REQUIRED_HELP_FLAGS = (
    "--repo",
    "--org",
    "--include",
    "--format",
    "--output",
    "--api-base-url",
)
FORBIDDEN_SECRET_FLAG = re.compile(
    r"--[a-z0-9][a-z0-9-]*(?:token|password|passwd|secret|credential|private-key)[a-z0-9-]*",
    re.IGNORECASE,
)


class VerificationError(RuntimeError):
    """A bounded, actionable conformance failure."""


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes
    elapsed_seconds: float


@dataclass(frozen=True)
class RepoFixture:
    full_name: str
    default_branch: str
    commit_sha: str
    tree_sha: str
    archived: bool
    private: bool
    files: Mapping[str, bytes]
    gitlinks: Mapping[str, str]


class _BoundedReader(threading.Thread):
    def __init__(
        self,
        stream: Any,
        limit: int,
        process: subprocess.Popen[bytes],
        exceeded: threading.Event,
    ) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._limit = limit
        self._process = process
        self._exceeded = exceeded
        self.data = bytearray()
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            while True:
                chunk = self._stream.read(8192)
                if not chunk:
                    return
                remaining = self._limit - len(self.data)
                if remaining > 0:
                    self.data.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self._exceeded.set()
                    self._process.kill()
                    return
        except BaseException as error:  # pragma: no cover - defensive thread path
            self.error = error
            self._exceeded.set()
            self._process.kill()


def _redact(data: bytes, secrets: Iterable[str]) -> bytes:
    rendered = data
    for secret in secrets:
        if secret:
            rendered = rendered.replace(secret.encode("utf-8"), b"[REDACTED]")
    return rendered


def run_bounded(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_seconds: float,
    secrets: Iterable[str] = (),
    max_capture_bytes: int = MAX_CAPTURE_BYTES,
) -> CommandResult:
    started = time.monotonic()
    process = subprocess.Popen(
        [str(item) for item in argv],
        cwd=cwd,
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    exceeded = threading.Event()
    stdout_reader = _BoundedReader(process.stdout, max_capture_bytes, process, exceeded)
    stderr_reader = _BoundedReader(process.stderr, max_capture_bytes, process, exceeded)
    stdout_reader.start()
    stderr_reader.start()
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        process.kill()
        process.wait(timeout=10)
        raise VerificationError(
            f"candidate exceeded {timeout_seconds:.0f}s: {' '.join(argv[:24])}"
        ) from error
    finally:
        stdout_reader.join(timeout=10)
        stderr_reader.join(timeout=10)

    if stdout_reader.error is not None:
        raise VerificationError(f"failed reading stdout: {stdout_reader.error}")
    if stderr_reader.error is not None:
        raise VerificationError(f"failed reading stderr: {stderr_reader.error}")
    if exceeded.is_set():
        raise VerificationError(
            f"candidate output exceeded {max_capture_bytes} bytes: {' '.join(argv[:24])}"
        )

    secret_values = tuple(secret for secret in secrets if secret)
    return CommandResult(
        argv=tuple(str(item) for item in argv),
        returncode=process.returncode,
        stdout=_redact(bytes(stdout_reader.data), secret_values),
        stderr=_redact(bytes(stderr_reader.data), secret_values),
        elapsed_seconds=time.monotonic() - started,
    )


def _git_blob_sha(content: bytes) -> str:
    return hashlib.sha1(f"blob {len(content)}\0".encode("ascii") + content).hexdigest()


def _sha40(label: str) -> str:
    return hashlib.sha1(label.encode("utf-8")).hexdigest()


def _compact_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _fixture_repositories() -> dict[str, RepoFixture]:
    hostile_requirement = '^1.0"]\nmalicious --> injected'
    root_manifest = f'''[package]
org = "acme"
name = "root"
version = "0.1.0"

[dependencies]
"acme/lib-a" = {json.dumps(hostile_requirement)}
"acme/lib-b" = "^1.0"
'''.encode("utf-8")
    root_lock = f'''version = 1

[[package]]
org = "acme"
name = "lib-a"
version = "1.0.0"
sha256 = "{'a' * 64}"

[[package]]
org = "acme"
name = "lib-b"
version = "1.0.0"
sha256 = "{'b' * 64}"

[[package]]
org = "acme"
name = "shared"
version = "2.0.0"
sha256 = "{'c' * 64}"
vcs_commit = "{_sha40('acme/shared-commit')}"
'''.encode("utf-8")
    gitmodules = b'''[submodule "vendor/shared"]
\tpath = vendor/shared
\turl = https://github.com/acme/shared.git
'''
    flake_nix = b'''{
  inputs.lib-a.url = "github:acme/lib-a";
  outputs = { self, lib-a }: {};
}
'''
    flake_lock = _compact_json(
        {
            "version": 7,
            "root": "root",
            "nodes": {
                "root": {"inputs": {"lib-a": "lib-a"}},
                "lib-a": {
                    "locked": {
                        "type": "github",
                        "owner": "acme",
                        "repo": "lib-a",
                        "rev": _sha40("acme/lib-a-commit"),
                        "narHash": "sha256-" + ("d" * 43),
                    },
                    "original": {"type": "github", "owner": "acme", "repo": "lib-a"},
                },
            },
        }
    )
    manifests = {
        "acme/lib-a": b'''[package]
org = "acme"
name = "lib-a"
version = "1.0.0"

[dependencies]
"acme/shared" = "^2.0"
''',
        "acme/lib-b": b'''[package]
org = "acme"
name = "lib-b"
version = "1.0.0"

[dependencies]
"acme/shared" = "^2.0"
''',
        "acme/shared": b'''[package]
org = "acme"
name = "shared"
version = "2.0.0"
''',
        "acme/cycle-a": b'''[package]
org = "acme"
name = "cycle-a"
version = "0.1.0"

[dependencies]
"acme/cycle-b" = "^0.1"
''',
        "acme/cycle-b": b'''[package]
org = "acme"
name = "cycle-b"
version = "0.1.0"

[dependencies]
"acme/cycle-a" = "^0.1"
''',
    }
    fixtures: dict[str, RepoFixture] = {
        "acme/root": RepoFixture(
            full_name="acme/root",
            default_branch="main",
            commit_sha=_sha40("acme/root-commit"),
            tree_sha=_sha40("acme/root-tree"),
            archived=False,
            private=True,
            files={
                ".zpkg.toml": root_manifest,
                ".zpkg.lock": root_lock,
                ".gitmodules": gitmodules,
                "flake.nix": flake_nix,
                "flake.lock": flake_lock,
            },
            gitlinks={"vendor/shared": _sha40("acme/shared-commit")},
        )
    }
    for full_name, manifest in manifests.items():
        fixtures[full_name] = RepoFixture(
            full_name=full_name,
            default_branch="main",
            commit_sha=_sha40(full_name + "-commit"),
            tree_sha=_sha40(full_name + "-tree"),
            archived=False,
            private=False,
            files={".zpkg.toml": manifest},
            gitlinks={},
        )
    fixtures["acme/archived"] = RepoFixture(
        full_name="acme/archived",
        default_branch="main",
        commit_sha=_sha40("acme/archived-commit"),
        tree_sha=_sha40("acme/archived-tree"),
        archived=True,
        private=False,
        files={"README.md": b"# archived fixture\n"},
        gitlinks={},
    )
    fixtures["acme/truncated"] = RepoFixture(
        full_name="acme/truncated",
        default_branch="main",
        commit_sha=_sha40("acme/truncated-commit"),
        tree_sha=_sha40("acme/truncated-tree"),
        archived=False,
        private=False,
        files={".zpkg.toml": manifests["acme/shared"]},
        gitlinks={},
    )
    return fixtures


class FakeGitHub:
    def __init__(self, token: str) -> None:
        self.token = token
        self.fixtures = _fixture_repositories()
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_type())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def _handler_type(self) -> type[BaseHTTPRequestHandler]:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "ZedPkgFakeGitHub/1"
            protocol_version = "HTTP/1.1"

            def log_message(self, _format: str, *_args: object) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
                outer.handle(self)

        return Handler

    def __enter__(self) -> "FakeGitHub":
        self.thread.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)

    def _record(self, handler: BaseHTTPRequestHandler) -> None:
        with self._lock:
            self.requests.append(
                {
                    "path": handler.path,
                    "authorization": handler.headers.get("Authorization"),
                }
            )

    def _send_json(
        self,
        handler: BaseHTTPRequestHandler,
        status: int,
        payload: Any,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        body = _compact_json(payload)
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Connection", "close")
        for name, value in (headers or {}).items():
            handler.send_header(name, value)
        handler.end_headers()
        handler.wfile.write(body)

    def _require_auth(self, handler: BaseHTTPRequestHandler) -> bool:
        authorization = handler.headers.get("Authorization", "")
        if authorization in {f"Bearer {self.token}", f"token {self.token}"}:
            return True
        self._send_json(
            handler,
            HTTPStatus.UNAUTHORIZED,
            {"message": "authentication required; credential is never echoed"},
        )
        return False

    def handle(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urllib.parse.urlsplit(handler.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        self._record(handler)
        if not self._require_auth(handler):
            return

        if path == "/orgs/acme/repos":
            page = int(query.get("page", ["1"])[0])
            names = sorted(
                name for name in self.fixtures if name != "acme/truncated"
            )
            midpoint = max(1, len(names) // 2)
            if page == 1:
                selected = names[:midpoint]
                headers = {
                    "Link": (
                        f'<{self.base_url}/orgs/acme/repos?per_page=100&page=2>; '
                        'rel="next"'
                    )
                }
            else:
                selected = names[midpoint:]
                headers = {}
            self._send_json(
                handler,
                HTTPStatus.OK,
                [self._metadata(self.fixtures[name]) for name in selected],
                headers=headers,
            )
            return

        if path == "/orgs/cross-origin/repos":
            self._send_json(
                handler,
                HTTPStatus.OK,
                [self._metadata(self.fixtures["acme/root"])],
                headers={"Link": '<https://example.invalid/repos?page=2>; rel="next"'},
            )
            return

        match = re.fullmatch(r"/repos/([^/]+/[^/]+)", path)
        if match:
            full_name = urllib.parse.unquote(match.group(1)).lower()
            if full_name == "acme/redirect":
                handler.send_response(HTTPStatus.FOUND)
                handler.send_header("Location", "https://example.invalid/secret")
                handler.send_header("Content-Length", "0")
                handler.send_header("Connection", "close")
                handler.end_headers()
                return
            fixture = self.fixtures.get(full_name)
            if fixture is None:
                self._send_json(handler, HTTPStatus.NOT_FOUND, {"message": "not found"})
                return
            self._send_json(handler, HTTPStatus.OK, self._metadata(fixture))
            return

        match = re.fullmatch(r"/repos/([^/]+/[^/]+)/branches/([^/]+)", path)
        if match:
            fixture = self._repo_or_404(handler, match.group(1))
            if fixture is not None:
                self._send_json(
                    handler,
                    HTTPStatus.OK,
                    {"name": urllib.parse.unquote(match.group(2)), "commit": {"sha": fixture.commit_sha}},
                )
            return

        match = re.fullmatch(r"/repos/([^/]+/[^/]+)/commits/([^/]+)", path)
        if match:
            fixture = self._repo_or_404(handler, match.group(1))
            if fixture is not None:
                self._send_json(
                    handler,
                    HTTPStatus.OK,
                    {"sha": fixture.commit_sha, "commit": {"tree": {"sha": fixture.tree_sha}}},
                )
            return

        match = re.fullmatch(r"/repos/([^/]+/[^/]+)/git/commits/([0-9a-fA-F]{40})", path)
        if match:
            fixture = self._repo_or_404(handler, match.group(1))
            if fixture is None:
                return
            if match.group(2).lower() != fixture.commit_sha:
                self._send_json(handler, HTTPStatus.NOT_FOUND, {"message": "unknown commit"})
            else:
                self._send_json(
                    handler,
                    HTTPStatus.OK,
                    {"sha": fixture.commit_sha, "tree": {"sha": fixture.tree_sha}},
                )
            return

        match = re.fullmatch(r"/repos/([^/]+/[^/]+)/git/trees/([0-9a-fA-F]{40})", path)
        if match:
            fixture = self._repo_or_404(handler, match.group(1))
            if fixture is None:
                return
            if match.group(2).lower() != fixture.tree_sha:
                self._send_json(handler, HTTPStatus.NOT_FOUND, {"message": "unknown tree"})
                return
            entries: list[dict[str, Any]] = []
            for file_path, content in sorted(fixture.files.items()):
                entries.append(
                    {
                        "path": file_path,
                        "mode": "100644",
                        "type": "blob",
                        "sha": _git_blob_sha(content),
                        "size": len(content),
                    }
                )
            for gitlink_path, commit_sha in sorted(fixture.gitlinks.items()):
                entries.append(
                    {
                        "path": gitlink_path,
                        "mode": "160000",
                        "type": "commit",
                        "sha": commit_sha,
                    }
                )
            self._send_json(
                handler,
                HTTPStatus.OK,
                {
                    "sha": fixture.tree_sha,
                    "truncated": fixture.full_name == "acme/truncated",
                    "tree": entries,
                },
            )
            return

        match = re.fullmatch(r"/repos/([^/]+/[^/]+)/git/blobs/([0-9a-fA-F]{40})", path)
        if match:
            fixture = self._repo_or_404(handler, match.group(1))
            if fixture is None:
                return
            requested = match.group(2).lower()
            for content in fixture.files.values():
                if _git_blob_sha(content) == requested:
                    self._send_json(
                        handler,
                        HTTPStatus.OK,
                        {
                            "sha": requested,
                            "encoding": "base64",
                            "size": len(content),
                            "content": base64.b64encode(content).decode("ascii"),
                        },
                    )
                    return
            self._send_json(handler, HTTPStatus.NOT_FOUND, {"message": "unknown blob"})
            return

        self._send_json(handler, HTTPStatus.NOT_FOUND, {"message": "unhandled endpoint"})

    def _repo_or_404(
        self, handler: BaseHTTPRequestHandler, encoded_full_name: str
    ) -> RepoFixture | None:
        fixture = self.fixtures.get(urllib.parse.unquote(encoded_full_name).lower())
        if fixture is None:
            self._send_json(handler, HTTPStatus.NOT_FOUND, {"message": "not found"})
        return fixture

    @staticmethod
    def _metadata(fixture: RepoFixture) -> dict[str, Any]:
        owner, name = fixture.full_name.split("/", 1)
        return {
            "id": int(hashlib.sha1(fixture.full_name.encode()).hexdigest()[:12], 16),
            "name": name,
            "full_name": fixture.full_name,
            "private": fixture.private,
            "archived": fixture.archived,
            "default_branch": fixture.default_branch,
            "owner": {"login": owner},
        }


def find_binary(workspace: Path) -> Path:
    executable = "zed.exe" if os.name == "nt" else "zed"
    for candidate in (
        workspace / "target" / "debug" / executable,
        workspace / "target" / "release" / executable,
    ):
        if candidate.is_file():
            return candidate.resolve()
    found = shutil.which("zed")
    if found:
        return Path(found).resolve()
    raise VerificationError("built zed binary not found under target/debug or target/release")


def _assert_no_secret(data: bytes, secret: str, context: str) -> None:
    if secret and secret.encode("utf-8") in data:
        raise VerificationError(f"credential leaked into {context}")


def verify_help(binary: Path, workspace: Path, env: Mapping[str, str]) -> CommandResult:
    result = run_bounded(
        [str(binary), "graph", "github", "--help"],
        cwd=workspace,
        env=env,
        timeout_seconds=20,
        secrets=(TOKEN_SENTINEL,),
    )
    if result.returncode != 0:
        raise VerificationError(
            "`zed graph github --help` failed: "
            + result.stderr.decode("utf-8", "replace")[:600]
        )
    help_text = (result.stdout + b"\n" + result.stderr).decode("utf-8", "replace")
    missing = [flag for flag in REQUIRED_HELP_FLAGS if flag not in help_text]
    if missing:
        raise VerificationError("production help is missing: " + ", ".join(missing))
    forbidden = sorted(set(FORBIDDEN_SECRET_FLAG.findall(help_text)))
    if forbidden:
        raise VerificationError(
            "secret-bearing command-line flags are forbidden: " + ", ".join(forbidden)
        )
    return result


def _run_inventory(
    binary: Path,
    workspace: Path,
    env: Mapping[str, str],
    *,
    output_format: str,
    output_path: Path,
    api_base_url: str | None,
    repositories: Sequence[str],
    organizations: Sequence[str],
    timeout_seconds: float,
    expected_codes: set[int] | None = None,
) -> CommandResult:
    argv = [str(binary), "graph", "github"]
    for repository in repositories:
        argv.extend(["--repo", repository])
    for organization in organizations:
        argv.extend(["--org", organization])
    argv.extend(
        [
            "--include",
            "nix,zed,git-submodule",
            "--include",
            "zed",
            "--format",
            output_format,
            "--output",
            str(output_path),
        ]
    )
    if api_base_url is not None:
        argv.extend(["--api-base-url", api_base_url])
    result = run_bounded(
        argv,
        cwd=workspace,
        env=env,
        timeout_seconds=timeout_seconds,
        secrets=(TOKEN_SENTINEL, env.get("ZED_PKG_GITHUB_TOKEN", "")),
    )
    allowed = expected_codes if expected_codes is not None else {0}
    if result.returncode not in allowed:
        raise VerificationError(
            f"candidate returned {result.returncode}, expected {sorted(allowed)} for "
            f"{output_format}: {result.stderr.decode('utf-8', 'replace')[:800]}"
        )
    _assert_no_secret(result.stdout, TOKEN_SENTINEL, "stdout")
    _assert_no_secret(result.stderr, TOKEN_SENTINEL, "stderr")
    return result


def _list(value: Mapping[str, Any], *names: str) -> list[Any]:
    for name in names:
        found = value.get(name)
        if isinstance(found, list):
            return found
    return []


def _edge_endpoint(edge: Mapping[str, Any], source: bool) -> str:
    names = ("source", "from", "source_id") if source else ("target", "to", "target_id")
    for name in names:
        value = edge.get(name)
        if value is not None:
            return str(value)
    return ""


def _node_coordinate(node: Mapping[str, Any]) -> str:
    package = node.get("package")
    if isinstance(package, str) and "/" in package:
        return package.lower()
    org = node.get("org", node.get("organization"))
    name = node.get("name", node.get("package_name"))
    if isinstance(org, str) and isinstance(name, str):
        return f"{org}/{name}".lower()
    node_id = str(node.get("id", "")).lower()
    match = re.search(r"(?:^|[:@])([a-z0-9_.-]+/[a-z0-9_.-]+)(?:$|[:@#])", node_id)
    return match.group(1) if match else ""


def _validate_json(raw: bytes, *, require_complete: bool) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
        inventory = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"invalid UTF-8 inventory JSON: {error}") from error
    if "\r" in text or not text.endswith("\n"):
        raise VerificationError("inventory JSON must use stable LF line endings")
    if not isinstance(inventory, dict):
        raise VerificationError("inventory root must be an object")
    if inventory.get("schema", inventory.get("resource")) != SCHEMA:
        raise VerificationError("unexpected inventory resource/schema")
    completeness = inventory.get("completeness")
    if not isinstance(completeness, dict) or completeness.get("resolution") != "not-claimed":
        raise VerificationError("inventory must report resolution=not-claimed")
    inventory_state = completeness.get("inventory")
    if require_complete and inventory_state not in {"complete", True}:
        raise VerificationError("deterministic fake inventory must be complete")

    nodes = _list(inventory, "nodes")
    edges = _list(inventory, "edges")
    pins = _list(inventory, "pins", "lock_pins")
    for pin in pins:
        if not isinstance(pin, dict) or pin.get("topological") is not False:
            raise VerificationError("every flat lock pin must set topological=false")
        artifact = pin.get("artifact_sha256", pin.get("sha256"))
        if artifact is not None and (
            not isinstance(artifact, str) or re.fullmatch(r"[0-9a-f]{64}", artifact) is None
        ):
            raise VerificationError("lock artifact SHA must be exact lowercase hex")
    for edge in edges:
        if isinstance(edge, dict) and "lock-pin" in str(edge.get("kind", "")):
            raise VerificationError("flat lock pins must never become topology edges")

    ids_by_coordinate = {
        _node_coordinate(node): str(node.get("id", ""))
        for node in nodes
        if isinstance(node, dict) and _node_coordinate(node)
    }
    root = ids_by_coordinate.get("acme/root")
    shared = ids_by_coordinate.get("acme/shared")
    if root and shared:
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            if (
                _edge_endpoint(edge, True) == root
                and _edge_endpoint(edge, False) == shared
                and str(edge.get("kind", "")).lower().startswith("zed")
            ):
                raise VerificationError(
                    "transitive shared selection fabricated root -> shared Zed topology"
                )

    components = _list(inventory, "strongly_connected_components", "sccs")
    analyzed: set[str] = set()
    for component in components:
        members = component.get("nodes", []) if isinstance(component, dict) else component
        if isinstance(members, list):
            analyzed.update(str(member) for member in members)
    node_ids = {str(node.get("id")) for node in nodes if isinstance(node, dict)}
    if components and analyzed != node_ids:
        raise VerificationError("SCC analysis must cover graph nodes only")

    cycle_a = ids_by_coordinate.get("acme/cycle-a")
    cycle_b = ids_by_coordinate.get("acme/cycle-b")
    if cycle_a and cycle_b:
        cycles = _list(inventory, "cycles")
        cycle_sets = []
        for cycle in cycles:
            members = cycle.get("nodes", []) if isinstance(cycle, dict) else cycle
            if isinstance(members, list):
                cycle_sets.append({str(member) for member in members})
        if {cycle_a, cycle_b} not in cycle_sets:
            raise VerificationError("cycle-a/cycle-b is missing from canonical cycles")

    return inventory


def _validate_visual(raw: bytes, output_format: str) -> None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise VerificationError(f"{output_format} is not UTF-8") from error
    if "\r" in text or not text.endswith("\n"):
        raise VerificationError(f"{output_format} must use stable LF line endings")
    lower = text.lower()
    if "lock pin" not in lower or ("evidence" not in lower and "topological=false" not in lower):
        raise VerificationError(f"{output_format} must render disconnected lock evidence")
    for line in text.splitlines():
        if "lock-pin" in line.lower() and ("->" in line or "-->" in line):
            raise VerificationError(f"{output_format} rendered a lock pin as an edge")
        if line.strip().startswith("malicious"):
            raise VerificationError(f"{output_format} label escaping allowed injection")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_fake_server(
    binary: Path,
    workspace: Path,
    base_env: Mapping[str, str],
    evidence: dict[str, Any],
) -> None:
    with FakeGitHub(TOKEN_SENTINEL) as fake:
        env = dict(base_env)
        env["ZED_PKG_GITHUB_TOKEN"] = TOKEN_SENTINEL
        env.pop("GITHUB_TOKEN", None)
        env.pop("ZED_PKG_GITHUB_ALLOW_TOKEN_TO_API_BASE", None)
        with tempfile.TemporaryDirectory(prefix="zpkg-graph-github-") as directory:
            root = Path(directory)
            summary: dict[str, Any] = {}
            for output_format in ("json", "dot", "mermaid"):
                first = root / f"first.{output_format}"
                second = root / f"second.{output_format}"
                common = {
                    "binary": binary,
                    "workspace": workspace,
                    "env": env,
                    "output_format": output_format,
                    "api_base_url": fake.base_url,
                    "repositories": ("ACME/ROOT", "acme/root"),
                    "organizations": ("ACME", "acme"),
                    "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
                }
                _run_inventory(output_path=first, **common)
                _run_inventory(output_path=second, **common)
                first_bytes = first.read_bytes()
                second_bytes = second.read_bytes()
                if first_bytes != second_bytes:
                    raise VerificationError(f"{output_format} changed across identical scans")
                if len(first_bytes) > MAX_EVIDENCE_BYTES:
                    raise VerificationError(f"{output_format} fixture output is unbounded")
                _assert_no_secret(first_bytes, TOKEN_SENTINEL, f"{output_format} file")
                if output_format == "json":
                    inventory = _validate_json(first_bytes, require_complete=True)
                    summary["counts"] = {
                        name: len(_list(inventory, name))
                        for name in ("repositories", "nodes", "edges", "pins", "failures", "contradictions", "cycles")
                    }
                else:
                    _validate_visual(first_bytes, output_format)
                summary[output_format] = {
                    "bytes": len(first_bytes),
                    "sha256": _sha256(first_bytes),
                }

            result = _run_inventory(
                binary,
                workspace,
                env,
                output_format="json",
                output_path=root / "cross-origin.json",
                api_base_url=fake.base_url,
                repositories=(),
                organizations=("cross-origin",),
                timeout_seconds=20,
                expected_codes={1, 2, 3},
            )
            if result.returncode == 0:
                raise VerificationError("cross-origin pagination was accepted")

            result = _run_inventory(
                binary,
                workspace,
                env,
                output_format="json",
                output_path=root / "redirect.json",
                api_base_url=fake.base_url,
                repositories=("acme/redirect",),
                organizations=(),
                timeout_seconds=20,
                expected_codes={1, 2, 3},
            )
            if result.returncode == 0:
                raise VerificationError("authorization-bearing redirect was accepted")

            truncated = root / "truncated.json"
            result = _run_inventory(
                binary,
                workspace,
                env,
                output_format="json",
                output_path=truncated,
                api_base_url=fake.base_url,
                repositories=("acme/truncated",),
                organizations=(),
                timeout_seconds=20,
                expected_codes={1},
            )
            if result.returncode != 1 or not truncated.is_file():
                raise VerificationError("truncated tree must emit an explicit partial artifact")
            partial = _validate_json(truncated.read_bytes(), require_complete=False)
            if partial.get("completeness", {}).get("inventory") not in {"partial", False}:
                raise VerificationError("truncated tree was not marked partial")
            failures = _list(partial, "failures")
            if not failures:
                raise VerificationError("truncated tree partial artifact has no failure")
            summary["truncated"] = {
                "bytes": truncated.stat().st_size,
                "sha256": _sha256(truncated.read_bytes()),
                "failure_count": len(failures),
            }

        requests = list(fake.requests)
        expected_auth = {f"Bearer {TOKEN_SENTINEL}", f"token {TOKEN_SENTINEL}"}
        if not requests or not all(item.get("authorization") in expected_auth for item in requests):
            raise VerificationError("loopback token routing did not follow the contract")
        if any(TOKEN_SENTINEL in str(item.get("path", "")) for item in requests):
            raise VerificationError("credential appeared in a request URL")
        summary["requests"] = {
            "count": len(requests),
            "unique_paths": len({str(item["path"]) for item in requests}),
        }
        evidence["fake_server"] = summary


def _bool_argument(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise argparse.ArgumentTypeError(f"invalid boolean value: {value!r}")


def verify_live_smoke(
    binary: Path,
    workspace: Path,
    base_env: Mapping[str, str],
    live_org: str,
    evidence: dict[str, Any],
) -> None:
    token = base_env.get("ZED_PKG_GITHUB_TOKEN", "")
    if not token:
        raise VerificationError("live smoke requested without ZED_PKG_GITHUB_TOKEN")
    with tempfile.TemporaryDirectory(prefix="zpkg-live-smoke-") as directory:
        root = Path(directory)
        summary: dict[str, Any] = {}
        for output_format in ("json", "dot", "mermaid"):
            first = root / f"live-first.{output_format}"
            second = root / f"live-second.{output_format}"
            common = {
                "binary": binary,
                "workspace": workspace,
                "env": base_env,
                "output_format": output_format,
                "api_base_url": None,
                "repositories": (),
                "organizations": (live_org,),
                "timeout_seconds": LIVE_TIMEOUT_SECONDS,
                "expected_codes": {0, 1},
            }
            _run_inventory(output_path=first, **common)
            _run_inventory(output_path=second, **common)
            first_bytes = first.read_bytes()
            if first_bytes != second.read_bytes():
                raise VerificationError(f"live {output_format} changed across repeated scans")
            _assert_no_secret(first_bytes, token, f"live {output_format} artifact")
            if output_format == "json":
                inventory = _validate_json(first_bytes, require_complete=False)
                summary["counts"] = {
                    name: len(_list(inventory, name))
                    for name in ("repositories", "nodes", "edges", "pins", "failures", "contradictions")
                }
                summary["inventory_completeness"] = inventory.get("completeness", {}).get("inventory")
            else:
                text = first_bytes.decode("utf-8")
                if "\r" in text or not text.endswith("\n"):
                    raise VerificationError(f"live {output_format} is not canonical UTF-8/LF")
            summary[output_format] = {
                "bytes": len(first_bytes),
                "sha256": _sha256(first_bytes),
            }
        evidence["live_smoke"] = summary


def write_evidence(path: Path, evidence: Mapping[str, Any]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    payload = _compact_json(evidence)
    if len(payload) > MAX_EVIDENCE_BYTES:
        raise VerificationError("redacted evidence summary exceeded bounded size")
    temporary = path / ".summary.json.tmp"
    temporary.write_bytes(payload)
    os.replace(temporary, path / "summary.json")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--run-live-smoke", type=_bool_argument, default=False)
    parser.add_argument("--live-org", default="zed-pkg-test")
    parser.add_argument("--evidence-dir", type=Path, default=Path(".graph-github-evidence"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    workspace = args.workspace.resolve()
    if not (workspace / "Cargo.toml").is_file():
        raise VerificationError(f"workspace is not a Rust package: {workspace}")
    binary = find_binary(workspace)
    env = os.environ.copy()
    env.setdefault("NO_COLOR", "1")
    env.setdefault("RUST_BACKTRACE", "0")
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("SOURCE_DATE_EPOCH", "0")

    help_result = verify_help(binary, workspace, env)
    evidence: dict[str, Any] = {
        "schema": "zpkg/graph-github-production-evidence/v1",
        "candidate": {
            "expected_sha": env.get("EXPECTED_CANDIDATE_SHA", ""),
            "binary_sha256": _sha256(binary.read_bytes()),
        },
        "help": {
            "bytes": len(help_result.stdout) + len(help_result.stderr),
            "sha256": _sha256(help_result.stdout + b"\n" + help_result.stderr),
        },
        "platform": {
            "os_name": os.name,
            "sys_platform": sys.platform,
            "python": sys.version.split()[0],
        },
    }
    verify_fake_server(binary, workspace, env, evidence)
    runner_os = env.get("RUNNER_OS", "")
    if args.run_live_smoke and runner_os.lower() == "linux":
        verify_live_smoke(binary, workspace, env, args.live_org, evidence)
    elif args.run_live_smoke:
        evidence["live_smoke"] = {
            "skipped": True,
            "reason": "live smoke is intentionally Linux-only",
        }
    write_evidence(args.evidence_dir.resolve(), evidence)
    print(
        "zed graph github production verification passed: "
        + _compact_json(
            {
                "candidate": evidence["candidate"]["expected_sha"],
                "fake_json": evidence["fake_server"]["json"]["sha256"],
                "live": "live_smoke" in evidence,
            }
        ).decode("utf-8").strip()
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as error:
        print(f"verification error: {error}", file=sys.stderr)
        raise SystemExit(1)
