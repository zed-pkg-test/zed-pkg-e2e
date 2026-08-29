#!/usr/bin/env python3
"""Live GitHub API canary: prove GitHub can host a Zed artifact when zpkg.net is down.

This is not the loopback CDN mock. It calls api.github.com and
uploads.github.com against github.com/zed-pkg-test/github-api-fallback-canary,
then downloads the same bytes back over the public Release URL with no
registry in the path.

Phases:

1. Ensure the public canary repo exists (create if missing).
2. Pack a disposable tarball (stdlib tarfile; no zed required).
3. Ensure a git tag + GitHub Release via the REST API.
4. Upload the tarball and a VersionMetadata sidecar as Release assets.
5. GET the repo, tags, release, and both download URLs; require byte-identical
   sha256. That is the 100% GitHub-API proof.
6. If --zed is passed, point the CLI at a closed loopback registry and require
   `zed install --frozen` to restore the payload through source fallback.

Token (never printed): GITHUB_TOKEN, GH_TOKEN, or ZED_PKG_GITHUB_TOKEN.
Needs `repo` (or fine-grained contents + metadata) on zed-pkg-test.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence


ORG = "zed-pkg-test"
REPO = "github-api-fallback-canary"
PACKAGE_NAME = "github-api-fallback-canary"
VERSION = "0.0.1"
TAG = f"v{VERSION}"
TARBALL_ASSET = f"zpkg-{ORG}-{PACKAGE_NAME}-{VERSION}.tar.gz"
SIDECAR_ASSET = f"zpkg-{ORG}-{PACKAGE_NAME}-{VERSION}.json"
API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
USER_AGENT = "zed-pkg-test-github-api-fallback/1.0"
PAYLOAD = "github-api-fallback\n"


def token_from_env() -> str:
    for key in ("ZED_PKG_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(key, "").strip()
        if value and value != "from-env":
            return value
    raise RuntimeError(
        "set GITHUB_TOKEN / GH_TOKEN / ZED_PKG_GITHUB_TOKEN (do not print it)"
    )


def api_request(
    method: str,
    url: str,
    token: str,
    *,
    body: bytes | None = None,
    content_type: str = "application/json",
    accept: str = "application/vnd.github+json",
) -> tuple[int, dict[str, str], bytes]:
    headers = {
        "Accept": accept,
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if body is not None:
        headers["Content-Type"] = content_type
        headers["Content-Length"] = str(len(body))
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as error:
        payload = error.read()
        return error.code, dict(error.headers.items()), payload


def api_json(method: str, url: str, token: str, payload: Any | None = None) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    status, _headers, raw = api_request(method, url, token, body=body)
    if status >= 400:
        snippet = raw.decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"{method} {url} -> {status}: {snippet}")
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def public_get(url: str) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def asset_names(org: str, name: str, version: str, ext: str = "tar.gz") -> list[str]:
    names = [f"zpkg-{org}-{name}-{version}.{ext}"]
    short = f"zpkg-{name}-{version}.{ext}"
    if short not in names:
        names.append(short)
    return names


def sidecar_names(org: str, name: str, version: str) -> list[str]:
    names = [f"zpkg-{org}-{name}-{version}.json"]
    short = f"zpkg-{name}-{version}.json"
    if short not in names:
        names.append(short)
    return names


def pack_canary(work: Path) -> tuple[bytes, Path]:
    """Pack the same `pkg/`-rooted layout `zed pack` writes.

    `zed install` rejects archives whose first directory is not `pkg/`.
    """
    root = work / "package"
    pkg = root / "pkg"
    (pkg / "src").mkdir(parents=True)
    (pkg / "src" / "payload.txt").write_text(PAYLOAD, encoding="utf-8")
    (pkg / ".zpkg.toml").write_text(
        f'''[package]
org = "{ORG}"
name = "{PACKAGE_NAME}"
version = "{VERSION}"
description = "Live GitHub API fallback canary"
license = "MIT"

[package.repository]
vcs = "git"
url = "https://github.com/{ORG}/{REPO}"

[install]
dir = "zed_modules"
adapter = "none"
''',
        encoding="utf-8",
    )
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        archive.add(pkg, arcname="pkg")
    tarball = buffer.getvalue()
    path = work / TARBALL_ASSET
    path.write_bytes(tarball)
    return tarball, path


def packed_member_names(tarball: bytes) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as archive:
        return [member.name for member in archive.getmembers()]


def ensure_repo(token: str) -> dict[str, Any]:
    status, _headers, raw = api_request(
        "GET", f"{API}/repos/{ORG}/{REPO}", token
    )
    if status == 200:
        repo = json.loads(raw.decode("utf-8"))
        print(f"repo exists: {repo['html_url']}", flush=True)
        return repo
    if status != 404:
        raise RuntimeError(f"GET repo returned {status}: {raw[:400]!r}")
    print(f"creating public repo {ORG}/{REPO}", flush=True)
    repo = api_json(
        "POST",
        f"{API}/orgs/{ORG}/repos",
        token,
        {
            "name": REPO,
            "description": "Live GitHub API canary: packed Zed artifact on Releases so registry.zpkg.net outages still install.",
            "private": False,
            "auto_init": True,
            "has_issues": False,
            "has_projects": False,
            "has_wiki": False,
        },
    )
    print(f"created {repo['html_url']}", flush=True)
    return repo


def ensure_manifest_on_default_branch(token: str, repo: dict[str, Any]) -> None:
    branch = repo.get("default_branch") or "main"
    status, _headers, raw = api_request(
        "GET",
        f"{API}/repos/{ORG}/{REPO}/contents/.zpkg.toml?ref={branch}",
        token,
    )
    if status == 200:
        return
    content = f'''[package]
org = "{ORG}"
name = "{PACKAGE_NAME}"
version = "{VERSION}"
description = "Live GitHub API fallback canary"
license = "MIT"

[package.repository]
vcs = "git"
url = "https://github.com/{ORG}/{REPO}"

[install]
dir = "zed_modules"
adapter = "none"
'''
    import base64

    api_json(
        "PUT",
        f"{API}/repos/{ORG}/{REPO}/contents/.zpkg.toml",
        token,
        {
            "message": "chore: add canary .zpkg.toml for GitHub fallback",
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        },
    )
    print(f"wrote .zpkg.toml on {branch}", flush=True)


def ensure_release(token: str, commitish: str) -> dict[str, Any]:
    status, _headers, raw = api_request(
        "GET", f"{API}/repos/{ORG}/{REPO}/releases/tags/{TAG}", token
    )
    if status == 200:
        release = json.loads(raw.decode("utf-8"))
        print(f"release exists: {release['html_url']}", flush=True)
        return release
    release = api_json(
        "POST",
        f"{API}/repos/{ORG}/{REPO}/releases",
        token,
        {
            "tag_name": TAG,
            "target_commitish": commitish,
            "name": TAG,
            "body": "Packed Zed canary for registry.zpkg.net outage fallback. Uploaded by scripts/github_api_fallback.py.",
            "draft": False,
            "prerelease": False,
        },
    )
    print(f"created release {release['html_url']}", flush=True)
    return release


def replace_asset(
    token: str, release: dict[str, Any], name: str, content_type: str, data: bytes
) -> None:
    for asset in release.get("assets") or []:
        if asset.get("name") == name:
            status, _headers, _raw = api_request(
                "DELETE", f"{API}/repos/{ORG}/{REPO}/releases/assets/{asset['id']}", token
            )
            if status not in (204, 200):
                raise RuntimeError(f"DELETE asset {name} returned {status}")
            print(f"deleted existing asset {name}", flush=True)
    encoded = urllib.parse.quote(name)
    url = (
        f"{UPLOADS}/repos/{ORG}/{REPO}/releases/{release['id']}/assets?name={encoded}"
    )
    status, _headers, raw = api_request(
        "POST",
        url,
        token,
        body=data,
        content_type=content_type,
        accept="application/vnd.github+json",
    )
    if status not in (201, 200):
        raise RuntimeError(f"upload {name} returned {status}: {raw[:400]!r}")
    print(f"uploaded {name} ({len(data)} bytes)", flush=True)


def prove_github_roundtrip(token: str, tarball: bytes, digest: str) -> dict[str, Any]:
    repo = api_json("GET", f"{API}/repos/{ORG}/{REPO}", token)
    if repo.get("private") is True:
        raise AssertionError("canary repo must be public so unauthenticated install works")
    tags = api_json("GET", f"{API}/repos/{ORG}/{REPO}/tags?per_page=100", token)
    tag_names = [item["name"] for item in tags]
    if TAG not in tag_names:
        raise AssertionError(f"tag {TAG} missing from GitHub tags API: {tag_names!r}")
    release = api_json("GET", f"{API}/repos/{ORG}/{REPO}/releases/tags/{TAG}", token)
    asset_map = {item["name"]: item for item in release.get("assets") or []}
    if TARBALL_ASSET not in asset_map:
        raise AssertionError(f"{TARBALL_ASSET} missing from release assets")
    if SIDECAR_ASSET not in asset_map:
        raise AssertionError(f"{SIDECAR_ASSET} missing from release assets")

    api_status, api_bytes = public_get(asset_map[TARBALL_ASSET]["url"])
    if api_status != 200:
        # browser_download_url is the unauthenticated path; the API url needs Accept
        api_status, _headers, api_bytes = api_request(
            "GET",
            asset_map[TARBALL_ASSET]["url"],
            token,
            accept="application/octet-stream",
        )
        if api_status != 200:
            raise AssertionError(f"API asset download returned {api_status}")
    if sha256_bytes(api_bytes) != digest:
        raise AssertionError("API asset sha256 did not match the uploaded tarball")

    public_url = (
        f"https://github.com/{ORG}/{REPO}/releases/download/{TAG}/{TARBALL_ASSET}"
    )
    public_status, public_bytes = public_get(public_url)
    if public_status != 200:
        raise AssertionError(f"public Release download returned {public_status}")
    if sha256_bytes(public_bytes) != digest:
        raise AssertionError("public Release download sha256 did not match")
    if public_bytes != tarball:
        raise AssertionError("public Release bytes drifted from the upload")

    sidecar_url = (
        f"https://github.com/{ORG}/{REPO}/releases/download/{TAG}/{SIDECAR_ASSET}"
    )
    sidecar_status, sidecar_bytes = public_get(sidecar_url)
    if sidecar_status != 200:
        raise AssertionError(f"sidecar download returned {sidecar_status}")
    sidecar = json.loads(sidecar_bytes.decode("utf-8"))
    if sidecar.get("sha256") != digest:
        raise AssertionError(f"sidecar sha256 {sidecar.get('sha256')} != {digest}")

    print(
        json.dumps(
            {
                "ok": True,
                "phase": "github-api",
                "repo": repo["html_url"],
                "release": release["html_url"],
                "tag": TAG,
                "sha256": digest,
                "public_tarball": public_url,
                "bytes": len(tarball),
            },
            indent=2,
        ),
        flush=True,
    )
    return sidecar


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


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


def zed_frozen_install(zed: Path, work: Path, sidecar: dict[str, Any]) -> None:
    consumer = work / "consumer"
    consumer.mkdir()
    (consumer / ".zpkg.toml").write_text(
        f'''[package]
org = "zed-pkg-test"
name = "github-api-fallback-consumer"
version = "0.0.0"
description = "Consumes the live GitHub API canary"

[package.repository]
vcs = "git"
url = "https://github.com/zed-pkg-test/github-api-fallback-consumer"

[install]
dir = "zed_modules"
adapter = "none"

[dependencies]
"{ORG}/{PACKAGE_NAME}" = "={VERSION}"
''',
        encoding="utf-8",
    )
    # The real lockfile schema is richer; let `zed install` write one first
    # against GitHub fallback, then frozen-restore from a wiped store.
    dead = f"http://127.0.0.1:{free_port()}"
    home = work / "zed-home"
    extra = {
        "ZED_PKG_SOURCE_FALLBACK": "true",
        "ZED_PKG_SOURCE_FALLBACK_ALLOW_LOOPBACK": "true",
        "ZED_PKG_R2_PUBLIC_BASE": "http://127.0.0.1:1",
    }
    run(
        [
            zed,
            "--registry",
            dead,
            "--home",
            home,
            "install",
            "--install-mode",
            "copy",
            "--adapter",
            "none",
            "--allow-ecosystem-mismatch",
        ],
        cwd=consumer,
        extra_env=extra,
    )
    payload = consumer / "zed_modules" / ORG / PACKAGE_NAME / "src" / "payload.txt"
    # Packed archive uses arcname PACKAGE-VERSION/src/payload.txt — install
    # layout depends on the CLI unpack. Accept either layout.
    if not payload.is_file():
        matches = list(consumer.rglob("payload.txt"))
        if not matches:
            raise AssertionError(f"zed install did not materialize payload under {consumer}")
        payload = matches[0]
    if payload.read_text(encoding="utf-8") != PAYLOAD:
        raise AssertionError("GitHub fallback install restored the wrong payload")
    lock_path = consumer / ".zpkg.lock"
    if not lock_path.is_file():
        raise AssertionError("install did not write a lockfile")
    lock_bytes = lock_path.read_bytes()
    shutil.rmtree(home)
    if (consumer / "zed_modules").exists():
        shutil.rmtree(consumer / "zed_modules")
    home_frozen = work / "zed-home-frozen"
    run(
        [
            zed,
            "--registry",
            dead,
            "--home",
            home_frozen,
            "install",
            "--frozen",
            "--install-mode",
            "copy",
            "--adapter",
            "none",
            "--allow-ecosystem-mismatch",
        ],
        cwd=consumer,
        extra_env=extra,
    )
    if lock_path.read_bytes() != lock_bytes:
        raise AssertionError("frozen GitHub fallback mutated the lockfile")
    restored = list(consumer.rglob("payload.txt"))
    if not restored or restored[0].read_text(encoding="utf-8") != PAYLOAD:
        raise AssertionError("frozen GitHub fallback did not restore the payload")
    print(
        json.dumps(
            {
                "ok": True,
                "phase": "zed-install",
                "registry": dead,
                "sha256": sidecar["sha256"],
            },
            indent=2,
        ),
        flush=True,
    )


def assert_contract_helpers() -> None:
    assert asset_names(ORG, PACKAGE_NAME, VERSION)[0] == TARBALL_ASSET
    assert sidecar_names(ORG, PACKAGE_NAME, VERSION)[0] == SIDECAR_ASSET
    assert TAG == "v0.0.1"
    packed, _path = pack_canary(Path(tempfile.mkdtemp(prefix="zpkg-canary-pack-")))
    names = packed_member_names(packed)
    assert any(name == "pkg" or name.startswith("pkg/") for name in names), names
    assert "pkg/.zpkg.toml" in names, names
    assert "pkg/src/payload.txt" in names, names


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--work-root",
        type=Path,
        required=True,
        help="fresh directory for the packed canary and optional zed homes",
    )
    parser.add_argument(
        "--zed",
        type=Path,
        help="optional zed binary with source-fallback; skipped if omitted",
    )
    parser.add_argument(
        "--skip-network",
        action="store_true",
        help="only check local naming contracts (PR syntax job)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    assert_contract_helpers()
    if args.skip_network:
        print(json.dumps({"ok": True, "phase": "contract"}), flush=True)
        return 0

    work = args.work_root.resolve()
    if work.exists():
        raise AssertionError(f"work root must be fresh: {work}")
    work.mkdir(parents=True)
    token = token_from_env()
    tarball, _path = pack_canary(work)
    digest = sha256_bytes(tarball)
    repo = ensure_repo(token)
    # auto_init races: wait until default branch exists
    for _ in range(10):
        fresh = api_json("GET", f"{API}/repos/{ORG}/{REPO}", token)
        if fresh.get("default_branch") and fresh.get("pushed_at"):
            repo = fresh
            break
        time.sleep(1)
    ensure_manifest_on_default_branch(token, repo)
    repo = api_json("GET", f"{API}/repos/{ORG}/{REPO}", token)
    branch = repo.get("default_branch") or "main"
    ref = api_json("GET", f"{API}/repos/{ORG}/{REPO}/git/ref/heads/{branch}", token)
    commit = ref["object"]["sha"]
    release = ensure_release(token, commit)
    sidecar = {
        "org": ORG,
        "name": PACKAGE_NAME,
        "version": VERSION,
        "sha256": digest,
        "size": len(tarball),
        "format": "tar.gz",
        "vcs_tag": TAG,
        "vcs_commit": commit,
        "download_url": f"https://github.com/{ORG}/{REPO}/releases/download/{TAG}/{TARBALL_ASSET}",
        "published_at": "1970-01-01T00:00:00Z",
        "yanked": False,
        "mirrors": [
            {
                "kind": "github-release",
                "url": f"https://github.com/{ORG}/{REPO}/releases/download/{TAG}/{TARBALL_ASSET}",
            }
        ],
    }
    replace_asset(token, release, TARBALL_ASSET, "application/gzip", tarball)
    replace_asset(
        token,
        release,
        SIDECAR_ASSET,
        "application/json",
        json.dumps(sidecar, indent=2).encode("utf-8"),
    )
    sidecar = prove_github_roundtrip(token, tarball, digest)
    if args.zed:
        zed = args.zed.resolve()
        if not zed.is_file():
            raise AssertionError(f"zed binary not found: {zed}")
        zed_frozen_install(zed, work, sidecar)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001
        print(f"github-api-fallback canary failed: {error}", file=sys.stderr)
        raise
