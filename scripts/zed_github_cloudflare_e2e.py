#!/usr/bin/env python3
"""Exercise the real zed publish/install path through GitHub and Cloudflare.

The write phase must run in the public canary repository so its repository-scoped
GITHUB_TOKEN can create Releases there. The harness deliberately points the
registry write at an unreachable non-loopback origin, requiring `zed publish`
to host the packed artifact and VersionMetadata sidecar on the canary's GitHub
Release. It independently proves four consumption paths:

1. the unauthenticated GitHub Release asset;
2. the Cloudflare CDN `/github/...` byte proxy;
3. `zed install` and `zed install --frozen` with both registry and R2 disabled,
   forcing direct GitHub source fallback; and
4. the Cloudflare registry metadata fallback.

The direct GitHub restore runs before the registry-edge assertion so one broken
surface cannot hide evidence for the other. No credential value is printed.
The script uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence

ORG = "zed-pkg-test"
REPO = "github-api-fallback-canary"
PACKAGE = "github-api-fallback-canary"
DEFAULT_VERSION = "0.0.3"
GITHUB_API = "https://api.github.com"
GITHUB_WEB = "https://github.com"
DEFAULT_REGISTRY = "https://registry.zpkg.net"
DEFAULT_CDN = "https://cdn.zpkg.net"
DEAD_REGISTRY = "https://registry-write-intentionally-unavailable.invalid"
DEAD_R2 = "http://127.0.0.1:1"
USER_AGENT = "zed-pkg-live-github-cloudflare-e2e/1.1"
VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"\s*$', re.MULTILINE)


def token_from_env() -> str:
    for key in ("ZED_PKG_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(key, "").strip()
        if value and value != "from-env":
            return value
    raise RuntimeError("a GitHub token is required but must never be printed")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def headers_lower(headers: Mapping[str, str]) -> dict[str, str]:
    return {key.lower(): value for key, value in headers.items()}


def request_bytes(
    url: str,
    *,
    token: str | None = None,
    accept: str = "application/octet-stream",
    timeout: int = 90,
) -> tuple[int, dict[str, str], bytes, str]:
    headers = {
        "Accept": accept,
        "User-Agent": USER_AGENT,
    }
    if token:
        headers.update(
            {
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return (
                response.status,
                headers_lower(dict(response.headers.items())),
                response.read(),
                response.geturl(),
            )
    except urllib.error.HTTPError as error:
        return (
            error.code,
            headers_lower(dict(error.headers.items())),
            error.read(),
            error.geturl(),
        )
    except urllib.error.URLError as error:
        raise RuntimeError(f"GET failed for {url}: {error.reason}") from error


def poll_bytes(
    url: str,
    *,
    accept: str = "application/octet-stream",
    attempts: int = 18,
    delay_seconds: float = 3.0,
) -> tuple[dict[str, str], bytes, str]:
    last_status = 0
    last_headers: dict[str, str] = {}
    last_body = b""
    for attempt in range(1, attempts + 1):
        status, headers, body, final = request_bytes(url, accept=accept)
        if status == 200:
            return headers, body, final
        last_status = status
        last_headers = headers
        last_body = body
        if attempt != attempts:
            time.sleep(delay_seconds)
    snippet = last_body.decode("utf-8", errors="replace")[:500]
    selected = {
        key: last_headers[key]
        for key in ("content-type", "retry-after", "x-zed-edge", "x-zed-source")
        if key in last_headers
    }
    raise RuntimeError(
        f"GET {url} never returned 200 "
        f"(last={last_status}, headers={selected!r}, body={snippet})"
    )


def run(
    command: Sequence[str | Path],
    *,
    cwd: Path,
    extra_env: Mapping[str, str] | None = None,
) -> str:
    argv = [str(value) for value in command]
    print(f"$ {' '.join(argv)}", flush=True)
    environment = os.environ.copy()
    environment.update(
        {
            "CI": "true",
            "GIT_TERMINAL_PROMPT": "0",
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
        raise RuntimeError(f"command failed ({completed.returncode}): {argv!r}")
    return completed.stdout


def package_contract(package_root: Path, version: str) -> str:
    manifest = package_root / ".zpkg.toml"
    payload = package_root / "src" / "payload.txt"
    if not manifest.is_file():
        raise AssertionError(f"missing canary manifest: {manifest}")
    if not payload.is_file():
        raise AssertionError(f"missing canary payload: {payload}")
    if (package_root / "product").exists():
        raise AssertionError(
            "the canary package checkout must be isolated from product source checkouts"
        )
    text = manifest.read_text(encoding="utf-8")
    match = VERSION_RE.search(text)
    if not match or match.group(1) != version:
        raise AssertionError(f"canary manifest version must be {version}")
    expected = (
        f'org = "{ORG}"',
        f'name = "{PACKAGE}"',
        f'url = "https://github.com/{ORG}/{REPO}"',
    )
    for value in expected:
        if value not in text:
            raise AssertionError(f"canary manifest is missing {value}")
    return payload.read_text(encoding="utf-8")


def publish_through_github(zed: Path, package_root: Path, work: Path) -> str:
    output = run(
        [
            zed,
            "--registry",
            DEAD_REGISTRY,
            "--home",
            work / "publisher-home",
            "publish",
            "--skip-vcs-checks",
            "--allow-dirty",
        ],
        cwd=package_root,
        extra_env={
            "ZED_PKG_SOURCE_FALLBACK": "true",
            "ZED_PKG_R2_PUBLIC_BASE": DEAD_R2,
        },
    )
    expected = f"mirrored {ORG}/{PACKAGE}@"
    if expected not in output or f"github.com/{ORG}/{REPO}" not in output:
        raise AssertionError("zed publish did not report a successful GitHub Release mirror")
    return output


def github_and_cdn_contract(
    token: str,
    version: str,
    cdn_base: str,
) -> dict[str, Any]:
    tag = f"v{version}"
    asset = f"zpkg-{ORG}-{PACKAGE}-{version}.tar.gz"
    sidecar_asset = f"zpkg-{ORG}-{PACKAGE}-{version}.json"
    release_url = f"{GITHUB_API}/repos/{ORG}/{REPO}/releases/tags/{tag}"

    release: dict[str, Any] | None = None
    for attempt in range(1, 19):
        status, _headers, raw, _final = request_bytes(
            release_url,
            token=token,
            accept="application/vnd.github+json",
        )
        if status == 200:
            release = json.loads(raw.decode("utf-8"))
            break
        if attempt != 18:
            time.sleep(3)
    if release is None:
        raise RuntimeError(f"GitHub release {tag} was not observable after publish")
    if release.get("draft") is True:
        raise AssertionError("canary Release must be public, not draft")
    assets = {item.get("name"): item for item in release.get("assets") or []}
    if asset not in assets or sidecar_asset not in assets:
        raise AssertionError(f"Release assets missing: have {sorted(str(key) for key in assets)}")

    direct_url = f"{GITHUB_WEB}/{ORG}/{REPO}/releases/download/{tag}/{asset}"
    sidecar_url = f"{GITHUB_WEB}/{ORG}/{REPO}/releases/download/{tag}/{sidecar_asset}"
    _direct_headers, direct_bytes, direct_final = poll_bytes(direct_url)
    _sidecar_headers, sidecar_bytes, _sidecar_final = poll_bytes(
        sidecar_url,
        accept="application/json",
    )
    metadata = json.loads(sidecar_bytes.decode("utf-8"))
    digest = sha256(direct_bytes)
    expected_metadata = {
        "org": ORG,
        "name": PACKAGE,
        "version": version,
        "sha256": digest,
        "size": len(direct_bytes),
        "download_url": direct_url,
    }
    for key, expected in expected_metadata.items():
        if metadata.get(key) != expected:
            raise AssertionError(
                f"sidecar {key} mismatch: {metadata.get(key)!r} != {expected!r}"
            )
    if not direct_final.startswith("https://"):
        raise AssertionError(f"GitHub asset redirected to a non-HTTPS URL: {direct_final}")

    cdn_url = f"{cdn_base.rstrip('/')}/github/{ORG}/{REPO}/{tag}/{asset}"
    cdn_headers, cdn_bytes, _cdn_final = poll_bytes(cdn_url)
    if cdn_bytes != direct_bytes:
        raise AssertionError("Cloudflare CDN bytes differ from the GitHub Release asset")
    if cdn_headers.get("x-zed-edge") != "cdn":
        raise AssertionError(f"unexpected CDN edge header: {cdn_headers.get('x-zed-edge')!r}")
    if cdn_headers.get("x-zed-source") != "github-release":
        raise AssertionError(
            f"unexpected CDN source header: {cdn_headers.get('x-zed-source')!r}"
        )
    if "immutable" not in cdn_headers.get("cache-control", ""):
        raise AssertionError("Cloudflare CDN response is not immutable")

    print(
        json.dumps(
            {
                "ok": True,
                "phase": "github-release-and-cloudflare-cdn",
                "release": release.get("html_url"),
                "version": version,
                "sha256": digest,
                "bytes": len(direct_bytes),
                "direct_github": direct_url,
                "cloudflare_cdn": cdn_url,
            },
            indent=2,
        ),
        flush=True,
    )
    return metadata


def registry_fallback_contract(
    version: str,
    registry_base: str,
    expected_metadata: Mapping[str, Any],
) -> None:
    registry_url = (
        f"{registry_base.rstrip('/')}/v1/packages/{ORG}/{PACKAGE}/versions/{version}"
    )
    registry_headers, raw, _final = poll_bytes(registry_url, accept="application/json")
    registry_metadata = json.loads(raw.decode("utf-8"))
    if registry_headers.get("x-zed-edge") != "registry":
        raise AssertionError(
            f"unexpected registry edge header: {registry_headers.get('x-zed-edge')!r}"
        )
    if registry_headers.get("x-zed-source") != "github-public":
        raise AssertionError(
            f"unexpected registry source header: {registry_headers.get('x-zed-source')!r}"
        )
    for key in ("org", "name", "version", "sha256", "size", "download_url"):
        if registry_metadata.get(key) != expected_metadata.get(key):
            raise AssertionError(
                f"registry metadata {key} differs from release sidecar: "
                f"{registry_metadata.get(key)!r} != {expected_metadata.get(key)!r}"
            )

    print(
        json.dumps(
            {
                "ok": True,
                "phase": "cloudflare-registry-public-fallback",
                "version": version,
                "sha256": expected_metadata["sha256"],
                "cloudflare_registry": registry_url,
            },
            indent=2,
        ),
        flush=True,
    )


def install_direct_from_github(
    zed: Path,
    work: Path,
    version: str,
    expected_payload: str,
    metadata: Mapping[str, Any],
) -> None:
    consumer = work / "consumer"
    consumer.mkdir(parents=True)
    (consumer / ".zpkg.toml").write_text(
        f'''[package]
org = "zed-pkg-test"
name = "github-cloudflare-e2e-consumer"
version = "0.0.0"
description = "Consumes the live GitHub fallback canary"

[package.repository]
vcs = "git"
url = "https://github.com/zed-pkg-test/github-cloudflare-e2e-consumer"

[install]
dir = "zed_modules"
adapter = "none"

[dependencies]
"{ORG}/{PACKAGE}" = "={version}"
''',
        encoding="utf-8",
    )
    home = work / "consumer-home"
    common_env = {
        "ZED_PKG_SOURCE_FALLBACK": "true",
        "ZED_PKG_R2_PUBLIC_BASE": DEAD_R2,
    }
    install_args: list[str | Path] = [
        zed,
        "--registry",
        DEAD_REGISTRY,
        "--home",
        home,
        "install",
        "--install-mode",
        "copy",
        "--adapter",
        "none",
        "--allow-ecosystem-mismatch",
    ]
    run(install_args, cwd=consumer, extra_env=common_env)
    payloads = list((consumer / "zed_modules").rglob("payload.txt"))
    if len(payloads) != 1:
        raise AssertionError(f"expected one installed payload, found {payloads}")
    if payloads[0].read_text(encoding="utf-8") != expected_payload:
        raise AssertionError("direct GitHub install restored the wrong payload")
    lock = consumer / ".zpkg.lock"
    if not lock.is_file():
        raise AssertionError("direct GitHub install did not create .zpkg.lock")
    lock_before = lock.read_bytes()
    lock_text = lock_before.decode("utf-8")
    if str(metadata["sha256"]) not in lock_text:
        raise AssertionError("lockfile does not pin the GitHub Release digest")

    shutil.rmtree(home)
    shutil.rmtree(consumer / "zed_modules")
    frozen_home = work / "consumer-home-frozen"
    frozen_args = install_args.copy()
    frozen_args[frozen_args.index(home)] = frozen_home
    frozen_args.insert(frozen_args.index("install") + 1, "--frozen")
    run(frozen_args, cwd=consumer, extra_env=common_env)
    if lock.read_bytes() != lock_before:
        raise AssertionError("frozen direct-GitHub install mutated .zpkg.lock")
    restored = list((consumer / "zed_modules").rglob("payload.txt"))
    if len(restored) != 1 or restored[0].read_text(encoding="utf-8") != expected_payload:
        raise AssertionError("frozen direct-GitHub install did not restore the payload")

    print(
        json.dumps(
            {
                "ok": True,
                "phase": "zed-direct-github-install",
                "version": version,
                "sha256": metadata["sha256"],
                "registry": DEAD_REGISTRY,
                "r2": DEAD_R2,
            },
            indent=2,
        ),
        flush=True,
    )


def assert_static_contract(version: str) -> None:
    assert version == DEFAULT_VERSION or re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+-]*", version)
    assert DEAD_REGISTRY.endswith(".invalid")
    assert f"zpkg-{ORG}-{PACKAGE}-{version}.tar.gz".startswith("zpkg-")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", type=Path, help="exact zed binary under test")
    parser.add_argument("--package-root", type=Path, help="checked-out canary repository")
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--registry-base", default=DEFAULT_REGISTRY)
    parser.add_argument("--cdn-base", default=DEFAULT_CDN)
    parser.add_argument("--skip-network", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    assert_static_contract(args.version)
    if args.skip_network:
        print(json.dumps({"ok": True, "phase": "contract"}), flush=True)
        return 0
    if args.zed is None or args.package_root is None:
        raise SystemExit("--zed and --package-root are required unless --skip-network is set")
    zed = args.zed.resolve()
    package_root = args.package_root.resolve()
    work = args.work_root.resolve()
    if not zed.is_file():
        raise AssertionError(f"zed binary not found: {zed}")
    if work.exists():
        raise AssertionError(f"work root must be fresh: {work}")
    work.mkdir(parents=True)

    expected_payload = package_contract(package_root, args.version)
    publish_through_github(zed, package_root, work)
    metadata = github_and_cdn_contract(
        token_from_env(),
        args.version,
        args.cdn_base,
    )
    install_direct_from_github(
        zed,
        work,
        args.version,
        expected_payload,
        metadata,
    )
    registry_fallback_contract(args.version, args.registry_base, metadata)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001
        print(f"zed GitHub + Cloudflare E2E failed: {error}", file=sys.stderr)
        raise
