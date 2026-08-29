#!/usr/bin/env python3
"""Live GHCR canary: prove GitHub Packages can host a Zed artifact.

Unlike github_api_fallback.py this does *not* create a Release. The CLI
source-fallback path tries a Release sidecar first, then GHCR. A package
that exists only on ghcr.io forces the GHCR branch.

Token (never printed): GITHUB_TOKEN / GH_TOKEN / ZED_PKG_GITHUB_TOKEN
with `write:packages` on zed-pkg-test.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ORG = "zed-pkg-test"
REPO = "ghcr-fallback-canary"
PACKAGE_NAME = "ghcr-fallback-canary"
VERSION = "0.0.1"
TAG = f"v{VERSION}"
API = "https://api.github.com"
GHCR = "https://ghcr.io"
USER_AGENT = "zed-pkg-test-ghcr-fallback/1.0"
PAYLOAD = "ghcr-fallback\n"
LAYER_MEDIA = "application/vnd.zed.package.v1.tar+gzip"
CONFIG_MEDIA = "application/vnd.zed.package.config.v1+json"
MANIFEST_MEDIA = "application/vnd.oci.image.manifest.v1+json"


def token_from_env() -> str:
    for key in ("ZED_PKG_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(key, "").strip()
        if value and value != "from-env":
            return value
    raise RuntimeError("set GITHUB_TOKEN / GH_TOKEN / ZED_PKG_GITHUB_TOKEN")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def api_request(
    method: str,
    url: str,
    token: str,
    *,
    body: bytes | None = None,
    content_type: str = "application/json",
    accept: str = "application/vnd.github+json",
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Accept": accept,
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
        },
    )
    if body is not None:
        request.add_header("Content-Type", content_type)
        request.add_header("Content-Length", str(len(body)))
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def api_json(method: str, url: str, token: str, payload: dict[str, Any] | None = None) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    status, _headers, raw = api_request(method, url, token, body=body)
    if status >= 300:
        raise RuntimeError(f"{method} {url} returned {status}: {raw[:400]!r}")
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def pack_canary(work: Path) -> tuple[bytes, Path]:
    pkg = work / "package" / "pkg"
    (pkg / "src").mkdir(parents=True)
    (pkg / "src" / "payload.txt").write_text(PAYLOAD, encoding="utf-8")
    (pkg / ".zpkg.toml").write_text(
        f'''[package]
org = "{ORG}"
name = "{PACKAGE_NAME}"
version = "{VERSION}"
description = "Live GHCR fallback canary"
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
    path = work / f"{PACKAGE_NAME}-{VERSION}.tar.gz"
    path.write_bytes(tarball)
    return tarball, path


def ensure_repo(token: str) -> dict[str, Any]:
    status, _headers, raw = api_request("GET", f"{API}/repos/{ORG}/{REPO}", token)
    if status == 200:
        repo = json.loads(raw.decode("utf-8"))
        print(f"repo exists: {repo['html_url']}", flush=True)
        return repo
    if status != 404:
        raise RuntimeError(f"GET repo returned {status}: {raw[:400]!r}")
    repo = api_json(
        "POST",
        f"{API}/orgs/{ORG}/repos",
        token,
        {
            "name": REPO,
            "description": "Live GHCR canary: packed Zed artifact on GitHub Packages, no Release.",
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
    import base64

    content = f'''[package]
org = "{ORG}"
name = "{PACKAGE_NAME}"
version = "{VERSION}"
description = "Live GHCR fallback canary"
license = "MIT"

[package.repository]
vcs = "git"
url = "https://github.com/{ORG}/{REPO}"

[install]
dir = "zed_modules"
adapter = "none"
'''
    api_json(
        "PUT",
        f"{API}/repos/{ORG}/{REPO}/contents/.zpkg.toml",
        token,
        {
            "message": "chore: add canary .zpkg.toml for GHCR fallback",
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        },
    )
    print(f"wrote .zpkg.toml on {branch}", flush=True)


def ensure_lightweight_tag(token: str, repo: dict[str, Any]) -> None:
    """Advertise version `v0.0.1` to the CLI resolver without a Release.

    `zed install` lists versions from git tags. A Release sidecar would win
    over GHCR, so this tag must stay a ref-only tag.
    """
    status, _headers, raw = api_request(
        "GET", f"{API}/repos/{ORG}/{REPO}/git/ref/tags/{TAG}", token
    )
    if status == 200:
        return
    default_branch = repo.get("default_branch") or "main"
    branch = api_json(
        "GET", f"{API}/repos/{ORG}/{REPO}/branches/{default_branch}", token
    )
    sha = (branch.get("commit") or {}).get("sha")
    if not sha:
        raise RuntimeError(f"missing sha for {default_branch}")
    created = api_json(
        "POST",
        f"{API}/repos/{ORG}/{REPO}/git/refs",
        token,
        {"ref": f"refs/tags/{TAG}", "sha": sha},
    )
    print(f"created lightweight tag {TAG} -> {sha[:12]}", flush=True)
    if not created.get("ref"):
        raise RuntimeError("git ref create returned no ref")


def github_login(token: str) -> str:
    status, _headers, raw = api_request("GET", f"{API}/user", token)
    if status != 200:
        raise RuntimeError(f"GET /user returned {status}: {raw[:200]!r}")
    login = json.loads(raw.decode("utf-8")).get("login")
    if not login:
        raise RuntimeError("GET /user missing login")
    return login


def ghcr_bearer(token: str, scope: str) -> str:
    # GHCR's /token endpoint treats a Bearer GitHub PAT as pull-only. Push
    # requires Docker-style Basic (username:pat). Never log the header.
    query = urllib.parse.urlencode({"service": "ghcr.io", "scope": scope})
    basic = base64.b64encode(f"{github_login(token)}:{token}".encode("ascii")).decode("ascii")
    request = urllib.request.Request(
        f"{GHCR}/token?{query}",
        headers={
            "Authorization": f"Basic {basic}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    if status != 200:
        raise RuntimeError(f"GHCR token returned {status}: {raw[:200]!r}")
    body = json.loads(raw.decode("utf-8"))
    bearer = body.get("token") or body.get("access_token")
    if not bearer:
        raise RuntimeError("GHCR token response missing token")
    return bearer


def ghcr_request(
    method: str,
    url: str,
    bearer: str,
    *,
    body: bytes | None = None,
    content_type: str | None = None,
    accept: str = "application/vnd.oci.image.manifest.v1+json",
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {bearer}",
            "User-Agent": USER_AGENT,
            "Accept": accept,
        },
    )
    if body is not None and content_type:
        request.add_header("Content-Type", content_type)
        request.add_header("Content-Length", str(len(body)))
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def put_blob(repo: str, bearer: str, digest: str, media: str, data: bytes) -> None:
    status, _headers, _raw = ghcr_request(
        "HEAD", f"{GHCR}/v2/{repo}/blobs/{digest}", bearer
    )
    if status == 200:
        return
    status, headers, raw = ghcr_request(
        "POST", f"{GHCR}/v2/{repo}/blobs/uploads/", bearer
    )
    if status not in {202, 201}:
        raise RuntimeError(f"start blob upload returned {status}: {raw[:200]!r}")
    location = headers.get("Location") or headers.get("location")
    if not location:
        raise RuntimeError("blob upload missing Location")
    if location.startswith("/"):
        location = f"{GHCR}{location}"
    sep = "&" if "?" in location else "?"
    status, _headers, raw = ghcr_request(
        "PUT",
        f"{location}{sep}digest={digest}",
        bearer,
        body=data,
        # GHCR rejects custom artifact media types on the blob PUT.
        # The real type lives on the manifest descriptor.
        content_type="application/octet-stream",
    )
    if status not in {201, 202}:
        raise RuntimeError(f"put blob {digest} returned {status}: {raw[:200]!r}")


def push_ghcr(token: str, tarball: bytes) -> dict[str, Any]:
    repo = f"{ORG}/{REPO}".lower()
    bearer = ghcr_bearer(token, f"repository:{repo}:push,pull")
    layer_digest = f"sha256:{sha256_hex(tarball)}"
    config = {
        "schema": "zpkg.oci-config/v1",
        "package": {"org": ORG, "name": PACKAGE_NAME, "version": VERSION},
        "repository": f"https://github.com/{ORG}/{REPO}",
        "vcs_tag": TAG,
        "artifact": {
            "format": "tar.gz",
            "digest": layer_digest,
            "size": len(tarball),
        },
    }
    config_bytes = json.dumps(config, separators=(",", ":")).encode("utf-8")
    config_digest = f"sha256:{sha256_hex(config_bytes)}"
    put_blob(repo, bearer, config_digest, CONFIG_MEDIA, config_bytes)
    put_blob(repo, bearer, layer_digest, LAYER_MEDIA, tarball)
    manifest = {
        "schemaVersion": 2,
        "mediaType": MANIFEST_MEDIA,
        "config": {
            "mediaType": CONFIG_MEDIA,
            "digest": config_digest,
            "size": len(config_bytes),
        },
        "layers": [
            {
                "mediaType": LAYER_MEDIA,
                "digest": layer_digest,
                "size": len(tarball),
            }
        ],
        "annotations": {
            "org.opencontainers.image.source": f"https://github.com/{ORG}/{REPO}",
            "dev.zed-pkg.package": f"{ORG}/{PACKAGE_NAME}",
            "dev.zed-pkg.vcs-tag": TAG,
        },
    }
    manifest_bytes = json.dumps(manifest, separators=(",", ":")).encode("utf-8")
    status, _headers, raw = ghcr_request(
        "PUT",
        f"{GHCR}/v2/{repo}/manifests/{TAG}",
        bearer,
        body=manifest_bytes,
        content_type=MANIFEST_MEDIA,
    )
    if status not in {201, 202}:
        raise RuntimeError(f"put manifest returned {status}: {raw[:300]!r}")
    # Public visibility so later anonymous/org pulls can work. Tokened canary
    # still succeeds if this PATCH is denied on a first-publish race.
    status, _headers, raw = api_request(
        "PATCH",
        f"{API}/orgs/{ORG}/packages/container/{REPO}",
        token,
        body=json.dumps({"visibility": "public"}).encode("utf-8"),
    )
    if status >= 300:
        print(f"note: package visibility PATCH returned {status}: {raw[:160]!r}", flush=True)
    return {
        "reference": f"ghcr.io/{repo}:{TAG}",
        "sha256": layer_digest.removeprefix("sha256:"),
        "size": len(tarball),
        "web": f"https://github.com/orgs/{ORG}/packages/container/package/{REPO}",
    }


def prove_ghcr_get(token: str, digest: str) -> None:
    repo = f"{ORG}/{REPO}".lower()
    bearer = ghcr_bearer(token, f"repository:{repo}:pull")
    status, _headers, raw = ghcr_request(
        "GET", f"{GHCR}/v2/{repo}/manifests/{TAG}", bearer
    )
    if status != 200:
        raise RuntimeError(f"GET GHCR manifest returned {status}: {raw[:200]!r}")
    manifest = json.loads(raw.decode("utf-8"))
    layer = manifest["layers"][0]
    if layer["digest"] != f"sha256:{digest}":
        raise RuntimeError(f"GHCR layer digest mismatch: {layer['digest']}")
    blob_status, _headers, blob = ghcr_request(
        "GET", f"{GHCR}/v2/{repo}/blobs/sha256:{digest}", bearer, accept="*/*"
    )
    if blob_status != 200:
        raise RuntimeError(f"GET GHCR blob returned {blob_status}")
    if sha256_hex(blob) != digest:
        raise RuntimeError("GHCR blob bytes do not match digest")


def run(command: list[str], cwd: Path, extra_env: dict[str, str] | None = None) -> str:
    print(f"$ {' '.join(command)}", flush=True)
    environment = os.environ.copy()
    environment.update({"CI": "true", "ZED_PKG_INTERACTIVE": "false"})
    if extra_env:
        environment.update(extra_env)
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    print(completed.stdout, end="", flush=True)
    if completed.returncode != 0:
        raise RuntimeError(f"command failed ({completed.returncode})")
    return completed.stdout


def zed_install(zed: Path, work: Path, digest: str, token: str) -> None:
    consumer = work / "consumer"
    consumer.mkdir()
    (consumer / ".zpkg.toml").write_text(
        f'''[package]
org = "zed-pkg-test"
name = "ghcr-fallback-consumer"
version = "0.0.0"

[package.repository]
vcs = "git"
url = "https://github.com/zed-pkg-test/ghcr-fallback-consumer"

[install]
dir = "zed_modules"
adapter = "none"

[dependencies]
"{ORG}/{PACKAGE_NAME}" = "={VERSION}"
''',
        encoding="utf-8",
    )
    dead = f"http://127.0.0.1:{free_port()}"
    extra = {
        "ZED_PKG_SOURCE_FALLBACK": "true",
        "ZED_PKG_SOURCE_FALLBACK_ALLOW_LOOPBACK": "true",
        "ZED_PKG_R2_PUBLIC_BASE": "http://127.0.0.1:1",
        "ZED_PKG_GITHUB_TOKEN": token,
    }
    home = work / "zed-home"
    run(
        [
            str(zed),
            "--registry",
            dead,
            "--home",
            str(home),
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
    matches = list(consumer.rglob("payload.txt"))
    if not matches or matches[0].read_text(encoding="utf-8") != PAYLOAD:
        raise AssertionError("zed install did not restore the GHCR payload")
    lock_path = consumer / ".zpkg.lock"
    if not lock_path.is_file():
        raise AssertionError("install did not write a lockfile")
    lock_bytes = lock_path.read_bytes()
    shutil.rmtree(home)
    if (consumer / "zed_modules").exists():
        shutil.rmtree(consumer / "zed_modules")
    run(
        [
            str(zed),
            "--registry",
            dead,
            "--home",
            str(work / "zed-home-frozen"),
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
        raise AssertionError("frozen GHCR install mutated the lockfile")
    restored = list(consumer.rglob("payload.txt"))
    if not restored or restored[0].read_text(encoding="utf-8") != PAYLOAD:
        raise AssertionError("frozen GHCR install did not restore the payload")
    print(
        json.dumps(
            {"ok": True, "phase": "zed-install", "registry": dead, "sha256": digest},
            indent=2,
        ),
        flush=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--zed", type=Path)
    parser.add_argument("--skip-network", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.skip_network:
        print(json.dumps({"ok": True, "phase": "contract"}), flush=True)
        return 0
    work = args.work_root.resolve()
    if work.exists():
        raise AssertionError(f"work root must be fresh: {work}")
    work.mkdir(parents=True)
    token = token_from_env()
    repo = ensure_repo(token)
    ensure_manifest_on_default_branch(token, repo)
    ensure_lightweight_tag(token, repo)
    tarball, _path = pack_canary(work)
    digest = sha256_hex(tarball)
    published = push_ghcr(token, tarball)
    if published["sha256"] != digest:
        raise AssertionError("published digest drifted from packed tarball")
    prove_ghcr_get(token, digest)
    print(json.dumps({"ok": True, "phase": "ghcr-api", **published}, indent=2), flush=True)
    if args.zed:
        zed_install(args.zed, work, digest, token)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001
        print(f"ghcr fallback canary failed: {error}", file=sys.stderr)
        raise SystemExit(1)
