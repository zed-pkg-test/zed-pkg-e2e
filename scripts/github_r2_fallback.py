#!/usr/bin/env python3
"""Black-box canary: registry down, public CDN still installs.

Publishes a disposable package through a file:// registry, copies the packed
tarball onto the guessable R2 keys (`packages/…` and `github/…`), takes the
HTTP registry down, and proves `zed install --frozen` fetches from a loopback
stand-in for `https://cdn.zpkg.net`. Also round-trips the oresoftware/api-docs
JSON call/receipt frame for `get_version` over TCP NDJSON.
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
from pathlib import Path
from typing import Mapping, Sequence


ORG = "zed-pkg-test"
NAME = "fallback-canary"
VERSION = "0.0.1"
TAG = f"v{VERSION}"
FILENAME = f"{NAME}-{VERSION}.tar.gz"


def shell_quote(value: str) -> str:
    if not value:
        return "''"
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", value):
        return value
    return "'" + value.replace("'", "'\\''") + "'"


def run(
    command: Sequence[str | Path],
    *,
    cwd: Path,
    extra_env: Mapping[str, str] | None = None,
) -> str:
    argv = [str(value) for value in command]
    print(f"\n$ (cd {cwd} && {' '.join(shell_quote(value) for value in argv)})", flush=True)
    environment = os.environ.copy()
    environment.update(
        {
            "CI": "true",
            "GIT_TERMINAL_PROMPT": "0",
            "ZED_PKG_TOKEN": "",
            "ZED_PKG_INTERACTIVE": "false",
        }
    )
    if extra_env:
        environment.update(extra_env)
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    print(completed.stdout, end="", flush=True)
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed with exit code {completed.returncode}: {argv!r}\n{completed.stdout}"
        )
    return completed.stdout


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def expand(template: str, **values: str) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{" + key + "}", value)
    return out


def load_route_map(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_package(directory: Path) -> None:
    directory.mkdir(parents=True)
    (directory / "src").mkdir()
    (directory / "src" / "payload.txt").write_text("cdn-fallback\n", encoding="utf-8")
    (directory / ".zpkg.toml").write_text(
        f'''[package]
org = "{ORG}"
name = "{NAME}"
version = "{VERSION}"
description = "Disposable GitHub/R2 fallback canary"
license = "MIT"

[package.repository]
vcs = "git"
url = "https://github.com/{ORG}/{NAME}"

[install]
dir = "zed_modules"
adapter = "none"
''',
        encoding="utf-8",
    )
    run(["git", "init", "-b", "main"], cwd=directory)
    run(["git", "config", "user.email", "canary@zed-pkg-test.invalid"], cwd=directory)
    run(["git", "config", "user.name", "zed-pkg-test canary"], cwd=directory)
    run(["git", "add", "-A"], cwd=directory)
    run(["git", "commit", "-m", "canary package"], cwd=directory)
    commit = run(["git", "rev-parse", "HEAD"], cwd=directory).strip()
    run(["git", "tag", TAG, commit], cwd=directory)


def write_consumer(directory: Path) -> None:
    directory.mkdir(parents=True)
    (directory / ".zpkg.toml").write_text(
        f'''[package]
org = "zed-pkg-test"
name = "fallback-consumer"
version = "0.0.0"
description = "Consumes the GitHub/R2 fallback canary"

[package.repository]
vcs = "git"
url = "https://github.com/zed-pkg-test/fallback-consumer"

[install]
dir = "zed_modules"
adapter = "none"

[dependencies]
"{ORG}/{NAME}" = "={VERSION}"
''',
        encoding="utf-8",
    )


def start_cdn(root: Path, port: int) -> http.server.ThreadingHTTPServer:
    class BoundHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            print(f"cdn: {format % args}", flush=True)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), BoundHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


class VersionRpcHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline()
        call = json.loads(raw)
        if call.get("v") != 1 or call.get("op") != "call" or call.get("key") != "get_version":
            receipt = {
                "v": 1,
                "op": "receipt",
                "id": call.get("id", ""),
                "key": call.get("key", ""),
                "transport": "tcp",
                "ok": False,
                "status": 400,
                "error": {"code": "bad_call"},
            }
        else:
            path = call.get("path") or {}
            receipt = {
                "v": 1,
                "op": "receipt",
                "id": call["id"],
                "key": "get_version",
                "transport": "tcp",
                "ok": True,
                "status": 200,
                "body": {
                    "org": path.get("org", ORG),
                    "name": path.get("name", NAME),
                    "version": path.get("version", VERSION),
                    "yanked": False,
                },
            }
        line = json.dumps(receipt, separators=(",", ":")) + "\n"
        self.wfile.write(line.encode("utf-8"))


def start_tcp_rpc(port: int) -> socketserver.ThreadingTCPServer:
    server = socketserver.ThreadingTCPServer(("127.0.0.1", port), VersionRpcHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def tcp_get_version(port: int) -> dict:
    call = {
        "v": 1,
        "op": "call",
        "id": "e2e-get-version",
        "key": "get_version",
        "transport": "tcp",
        "path": {"org": ORG, "name": NAME, "version": VERSION},
    }
    payload = json.dumps(call, separators=(",", ":")) + "\n"
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(payload.encode("utf-8"))
        sock.shutdown(socket.SHUT_WR)
        raw = b""
        while not raw.endswith(b"\n"):
            chunk = sock.recv(4096)
            if not chunk:
                break
            raw += chunk
    receipt = json.loads(raw.decode("utf-8"))
    if not receipt.get("ok"):
        raise AssertionError(f"tcp get_version failed: {receipt!r}")
    return receipt


def copy_guessable_objects(artifact: Path, cdn_root: Path) -> None:
    targets = [
        cdn_root / "packages" / ORG / NAME / VERSION / FILENAME,
        cdn_root / "github" / ORG / NAME / TAG / FILENAME,
    ]
    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(artifact, target)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument(
        "--route-map",
        type=Path,
        help="zed-api.route-map.json (defaults to a sibling zed-interfaces checkout)",
    )
    parser.add_argument(
        "--interfaces-generated-ts",
        type=Path,
        help="zed-interfaces generated TypeScript to compare with zed-clients",
    )
    parser.add_argument(
        "--clients-generated-ts",
        type=Path,
        help="zed-clients copy of the generated TypeScript routes",
    )
    return parser.parse_args()


def assert_route_map(route_map: dict) -> None:
    mapping = route_map["map"]
    get_version = mapping["get_version"]
    if get_version["path"] != "/v1/packages/{org}/{name}/versions/{version}":
        raise AssertionError(get_version["path"])
    if get_version["transports"] != ["http", "tcp"]:
        raise AssertionError(get_version["transports"])
    if get_version.get("tcp_framing") != "ndjson":
        raise AssertionError("get_version must use ndjson on TCP")
    if mapping["registry_events"]["transports"] != ["websocket"]:
        raise AssertionError(mapping["registry_events"])
    package_path = expand(mapping["get_package"]["path"], org=ORG, name=NAME)
    if package_path != f"/v1/packages/{ORG}/{NAME}":
        raise AssertionError(package_path)
    cdn_path = expand(
        mapping["cdn_package_object"]["path"],
        org=ORG,
        name=NAME,
        version=VERSION,
        filename=FILENAME,
    )
    if cdn_path != f"/packages/{ORG}/{NAME}/{VERSION}/{FILENAME}":
        raise AssertionError(cdn_path)


def strip_ts_banner(text: str) -> str:
    return re.sub(r"^/\*\*.*?\*/\s*", "", text, count=1, flags=re.S).strip() + "\n"


def parse_args_and_compare_generated(args: argparse.Namespace) -> None:
    if args.interfaces_generated_ts and args.clients_generated_ts:
        left = strip_ts_banner(args.interfaces_generated_ts.read_text(encoding="utf-8"))
        right = strip_ts_banner(args.clients_generated_ts.read_text(encoding="utf-8"))
        if left != right:
            raise AssertionError(
                "zed-clients generated routes drifted from zed-interfaces generated TypeScript"
            )


def main() -> int:
    args = parse_args()
    zed = args.zed.resolve()
    root = args.work_root.resolve()
    if root.exists():
        raise AssertionError(f"work root must be fresh: {root}")
    if not zed.is_file():
        raise AssertionError(f"zed binary not found: {zed}")

    default_map = (
        Path(__file__).resolve().parents[3]
        / "zed-pkg"
        / "zed-interfaces"
        / "route-maps"
        / "zed-api.route-map.json"
    )
    route_map_path = (args.route_map or default_map).resolve()
    if not route_map_path.is_file():
        raise AssertionError(f"route map not found: {route_map_path}")
    route_map = load_route_map(route_map_path)
    assert_route_map(route_map)
    parse_args_and_compare_generated(args)

    root.mkdir(parents=True)
    registry = root / "file-registry"
    registry.mkdir()
    registry_url = f"file://{registry}"
    package = root / "package"
    consumer = root / "consumer"
    publish_home = root / "publish-home"
    install_home = root / "install-home"
    fallback_home = root / "fallback-home"
    cdn_root = root / "cdn"
    cdn_root.mkdir()

    write_package(package)
    write_consumer(consumer)

    def zed_cmd(*command: str | Path, cwd: Path, home: Path, extra_env: Mapping[str, str] | None = None) -> str:
        return run(
            [zed, "--registry", registry_url, "--home", home, *command],
            cwd=cwd,
            extra_env=extra_env,
        )

    zed_cmd("publish", cwd=package, home=publish_home)
    metadata_path = registry / "packages" / ORG / NAME / "versions" / f"{VERSION}.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    sha256 = str(metadata["sha256"])
    artifact = registry / "artifacts" / f"{sha256}.tar.gz"
    if not artifact.is_file():
        raise AssertionError(f"published artifact missing: {artifact}")
    copy_guessable_objects(artifact, cdn_root)
    content_copy = cdn_root / "artifacts" / f"{sha256}.tar.gz"
    content_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(artifact, content_copy)

    zed_cmd(
        "install",
        "--install-mode",
        "copy",
        "--adapter",
        "none",
        "--allow-ecosystem-mismatch",
        cwd=consumer,
        home=install_home,
    )
    lock = consumer / ".zpkg.lock"
    lock_bytes = lock.read_bytes()
    payload = consumer / "zed_modules" / ORG / NAME / "src" / "payload.txt"
    if payload.read_text(encoding="utf-8") != "cdn-fallback\n":
        raise AssertionError("file-registry install did not materialize the canary")
    zed_cmd("uninstall", cwd=consumer, home=install_home)
    if lock.read_bytes() != lock_bytes:
        raise AssertionError("uninstall mutated the lock")
    shutil.rmtree(install_home)
    if (consumer / "zed_modules").exists():
        raise AssertionError("uninstall left materialization behind")

    dead_port = free_port()
    cdn_port = free_port()
    tcp_port = free_port()
    cdn = start_cdn(cdn_root, cdn_port)
    rpc = start_tcp_rpc(tcp_port)
    try:
        receipt = tcp_get_version(tcp_port)
        if receipt["body"]["version"] != VERSION:
            raise AssertionError(receipt)
        if receipt["transport"] != "tcp":
            raise AssertionError(receipt)

        fallback_registry = f"http://127.0.0.1:{dead_port}"
        cdn_base = f"http://127.0.0.1:{cdn_port}"
        run(
            [
                zed,
                "--registry",
                fallback_registry,
                "--home",
                fallback_home,
                "--r2-public-base",
                cdn_base,
                "install",
                "--frozen",
                "--install-mode",
                "copy",
                "--adapter",
                "none",
                "--allow-ecosystem-mismatch",
            ],
            cwd=consumer,
            extra_env={
                "ZED_PKG_SOURCE_FALLBACK": "true",
                "ZED_PKG_SOURCE_FALLBACK_ALLOW_LOOPBACK": "true",
                "ZED_PKG_R2_PUBLIC_BASE": cdn_base,
            },
        )
        restored = consumer / "zed_modules" / ORG / NAME / "src" / "payload.txt"
        if restored.read_text(encoding="utf-8") != "cdn-fallback\n":
            raise AssertionError("CDN fallback did not restore the packed payload")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        if digest != sha256:
            raise AssertionError("packed artifact digest drifted")
        print(
            json.dumps(
                {
                    "ok": True,
                    "registry": fallback_registry,
                    "cdn": cdn_base,
                    "tcp_rpc": f"127.0.0.1:{tcp_port}",
                    "sha256": sha256,
                    "cdn_keys": [
                        f"packages/{ORG}/{NAME}/{VERSION}/{FILENAME}",
                        f"github/{ORG}/{NAME}/{TAG}/{FILENAME}",
                        f"artifacts/{sha256}.tar.gz",
                    ],
                },
                indent=2,
            ),
            flush=True,
        )
    finally:
        cdn.shutdown()
        rpc.shutdown()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001 — canary must print the failure
        print(f"github-r2-fallback canary failed: {error}", file=sys.stderr)
        raise
