#!/usr/bin/env python3
"""Credential-free Zed fallback attestation using zed-pkg-test fixtures.

The harness never publishes or mutates GitHub/Cloudflare state. It proves two
immutable GitHub Release fixtures, the public Cloudflare CDN byte path, safe
HEAD/range/negative behavior, and (optionally) a real zed CLI install while the
configured registry is an unreachable loopback endpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping

from github_api_fallback import zed_frozen_install

USER_AGENT = "zed-pkg-test-public-fallback/1.0"
MAX_JSON = 1024 * 1024
MAX_HTML = 2 * 1024 * 1024
MAX_ARTIFACT = 110 * 1024 * 1024
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def lower_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in headers.items()}


def request(
    url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    max_bytes: int = MAX_JSON,
    attempts: int = 5,
) -> tuple[int, dict[str, str], bytes, str]:
    merged = {"User-Agent": USER_AGENT, **dict(headers or {})}
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(url, method=method, headers=merged)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise AssertionError(f"response exceeded {max_bytes} bytes: {url}")
                return (
                    int(response.status),
                    lower_headers(response.headers),
                    body,
                    str(response.geturl()),
                )
        except urllib.error.HTTPError as error:
            body = error.read(max_bytes + 1)
            if len(body) > max_bytes:
                body = body[:max_bytes]
            status = int(error.code)
            if status < 500 or attempt == attempts:
                return status, lower_headers(error.headers), body, str(error.geturl())
            last_error = RuntimeError(f"{url} returned HTTP {status}")
        except (OSError, TimeoutError, urllib.error.URLError) as error:
            last_error = error
            if attempt == attempts:
                break
        time.sleep(attempt * 0.5)
    raise RuntimeError(f"request failed after {attempts} attempts: {url}: {last_error}")


def json_body(body: bytes, label: str) -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AssertionError(f"{label} did not return valid JSON: {error}") from error


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def proof_headers(headers: Mapping[str, str]) -> dict[str, str]:
    names = (
        "cache-control",
        "cf-cache-status",
        "cf-ray",
        "content-length",
        "content-range",
        "content-type",
        "etag",
        "location",
        "server",
        "x-zed-edge",
        "x-zed-source",
        "x-zpkg-mirror",
    )
    return {name: headers[name] for name in names if name in headers}


def require_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("schema") != "zed-pkg-test.public-edge-fallback-canaries/v1":
        raise AssertionError("unexpected canary schema")
    repo = raw.get("repository")
    canaries = raw.get("canaries")
    if not isinstance(repo, dict) or not isinstance(canaries, list) or len(canaries) < 2:
        raise AssertionError("canary ledger requires repository metadata and at least two versions")
    for key in ("org", "repo", "package"):
        if not isinstance(repo.get(key), str) or not repo[key]:
            raise AssertionError(f"repository.{key} is required")
    seen: set[str] = set()
    for item in canaries:
        if not isinstance(item, dict):
            raise AssertionError("each canary must be an object")
        version = item.get("version")
        if not isinstance(version, str) or version in seen:
            raise AssertionError(f"invalid or duplicate canary version: {version!r}")
        seen.add(version)
        if item.get("tag") != f"v{version}":
            raise AssertionError(f"canary {version} tag must be v{version}")
        if not isinstance(item.get("bytes"), int) or item["bytes"] <= 0:
            raise AssertionError(f"canary {version} bytes must be positive")
        if not isinstance(item.get("sha256"), str) or not SHA256.fullmatch(item["sha256"]):
            raise AssertionError(f"canary {version} has invalid sha256")
        for key in ("asset", "sidecar"):
            if not isinstance(item.get(key), str) or "/" in item[key] or ".." in item[key]:
                raise AssertionError(f"canary {version} has unsafe {key}")
    return raw


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--zed", type=Path)
    parser.add_argument("--cli-only", action="store_true")
    args = parser.parse_args()

    config = require_config(json.loads(args.config.read_text(encoding="utf-8")))
    repo = config["repository"]
    canaries: list[dict[str, Any]] = config["canaries"]
    enforce_registry = os.environ.get("ENFORCE_LIVE_REGISTRY", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    evidence: dict[str, Any] = {
        "schema": "zed-pkg-test.public-edge-fallback-attestation/v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_sha": os.environ.get("GITHUB_SHA"),
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "checks": {},
    }
    failures: list[str] = []

    def record(name: str, required: bool, operation: Callable[[], dict[str, Any]]) -> None:
        try:
            result = operation()
            ok = result.get("ok", True) is not False
            evidence["checks"][name] = {"ok": ok, "required": required, **result}
            if required and not ok:
                failures.append(name)
        except Exception as error:  # noqa: BLE001 - evidence must survive every boundary failure
            evidence["checks"][name] = {
                "ok": False,
                "required": required,
                "error": str(error),
            }
            if required:
                failures.append(name)

    direct: dict[str, tuple[dict[str, Any], bytes]] = {}

    def prove_github_repository() -> dict[str, Any]:
        url = f"https://api.github.com/repos/{repo['org']}/{repo['repo']}"
        status, headers, body, final_url = request(
            url,
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        )
        metadata = json_body(body, "GitHub repository")
        ok = (
            status == 200
            and metadata.get("private") is False
            and metadata.get("visibility") == "public"
            and str(metadata.get("full_name", "")).lower()
            == f"{repo['org']}/{repo['repo']}".lower()
        )
        return {
            "ok": ok,
            "status": status,
            "final_url": final_url,
            "headers": proof_headers(headers),
            "full_name": metadata.get("full_name"),
            "visibility": metadata.get("visibility"),
        }

    if not args.cli_only:
        record("github_repository_public", True, prove_github_repository)

        for item in canaries:
            version = item["version"]
            tag = item["tag"]
            release_base = f"https://github.com/{repo['org']}/{repo['repo']}/releases/download/{tag}"
            asset_url = f"{release_base}/{item['asset']}"
            sidecar_url = f"{release_base}/{item['sidecar']}"
            cdn_url = f"https://cdn.zpkg.net/github/{repo['org']}/{repo['repo']}/{tag}/{item['asset']}"

            def github_release(item: dict[str, Any] = item, tag: str = tag) -> dict[str, Any]:
                url = f"https://api.github.com/repos/{repo['org']}/{repo['repo']}/releases/tags/{tag}"
                status, headers, body, final_url = request(
                    url,
                    headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
                )
                release = json_body(body, f"GitHub release {tag}")
                assets = {asset.get("name"): asset for asset in release.get("assets", [])}
                artifact = assets.get(item["asset"], {})
                sidecar_asset = assets.get(item["sidecar"], {})
                digest = str(artifact.get("digest", "")).removeprefix("sha256:")
                ok = (
                    status == 200
                    and release.get("draft") is False
                    and release.get("tag_name") == tag
                    and digest == item["sha256"]
                    and artifact.get("size") == item["bytes"]
                    and bool(sidecar_asset)
                )
                return {
                    "ok": ok,
                    "status": status,
                    "final_url": final_url,
                    "headers": proof_headers(headers),
                    "release_id": release.get("id"),
                    "asset_digest": digest,
                    "asset_bytes": artifact.get("size"),
                }

            def direct_release(
                item: dict[str, Any] = item,
                asset_url: str = asset_url,
                sidecar_url: str = sidecar_url,
            ) -> dict[str, Any]:
                side_status, side_headers, side_bytes, side_final = request(
                    sidecar_url,
                    headers={"Accept": "application/json"},
                    max_bytes=MAX_JSON,
                )
                asset_status, asset_headers, asset_bytes, asset_final = request(
                    asset_url,
                    headers={"Accept": "application/octet-stream"},
                    max_bytes=MAX_ARTIFACT,
                )
                sidecar_data = json_body(side_bytes, f"sidecar {item['version']}")
                digest = sha256(asset_bytes)
                ok = (
                    side_status == 200
                    and asset_status == 200
                    and digest == item["sha256"]
                    and len(asset_bytes) == item["bytes"]
                    and sidecar_data.get("org") == repo["org"]
                    and sidecar_data.get("name") == repo["package"]
                    and sidecar_data.get("version") == item["version"]
                    and sidecar_data.get("sha256") == item["sha256"]
                    and sidecar_data.get("size") == item["bytes"]
                    and sidecar_data.get("download_url") == asset_url
                )
                if ok:
                    direct[item["version"]] = (sidecar_data, asset_bytes)
                return {
                    "ok": ok,
                    "sidecar_status": side_status,
                    "archive_status": asset_status,
                    "sidecar_final_url": side_final,
                    "archive_final_url": asset_final,
                    "sidecar_headers": proof_headers(side_headers),
                    "archive_headers": proof_headers(asset_headers),
                    "sha256": digest,
                    "bytes": len(asset_bytes),
                }

            def cloudflare_get(
                item: dict[str, Any] = item,
                cdn_url: str = cdn_url,
            ) -> dict[str, Any]:
                if item["version"] not in direct:
                    raise AssertionError("direct GitHub proof must pass before CDN comparison")
                expected_sidecar, expected_bytes = direct[item["version"]]
                status, headers, body, final_url = request(
                    cdn_url,
                    headers={"Accept": "application/octet-stream"},
                    max_bytes=MAX_ARTIFACT,
                )
                digest = sha256(body)
                ok = (
                    status == 200
                    and final_url == cdn_url
                    and headers.get("server", "").lower() == "cloudflare"
                    and headers.get("x-zed-edge") == "cdn"
                    and headers.get("x-zed-source") == "github-release"
                    and headers.get("x-zpkg-mirror") == "github-release"
                    and "immutable" in headers.get("cache-control", "")
                    and body == expected_bytes
                    and digest == expected_sidecar["sha256"]
                )
                return {
                    "ok": ok,
                    "status": status,
                    "final_url": final_url,
                    "headers": proof_headers(headers),
                    "sha256": digest,
                    "bytes": len(body),
                    "byte_identical_to_github": body == expected_bytes,
                }

            def cloudflare_head(
                item: dict[str, Any] = item,
                cdn_url: str = cdn_url,
            ) -> dict[str, Any]:
                status, headers, body, final_url = request(
                    cdn_url,
                    method="HEAD",
                    headers={"Accept": "application/octet-stream"},
                    max_bytes=1,
                )
                ok = (
                    status == 200
                    and not body
                    and final_url == cdn_url
                    and headers.get("x-zed-edge") == "cdn"
                    and headers.get("x-zed-source") == "github-release"
                    and int(headers.get("content-length", "0")) == item["bytes"]
                    and "immutable" in headers.get("cache-control", "")
                )
                return {
                    "ok": ok,
                    "status": status,
                    "final_url": final_url,
                    "headers": proof_headers(headers),
                }

            def cloudflare_range(
                item: dict[str, Any] = item,
                cdn_url: str = cdn_url,
            ) -> dict[str, Any]:
                if item["version"] not in direct:
                    raise AssertionError("direct GitHub proof must pass before range comparison")
                _sidecar, expected_bytes = direct[item["version"]]
                status, headers, body, final_url = request(
                    cdn_url,
                    headers={"Accept": "application/octet-stream", "Range": "bytes=0-127"},
                    max_bytes=MAX_ARTIFACT,
                )
                partial = (
                    status == 206
                    and body == expected_bytes[:128]
                    and headers.get("content-range")
                    == f"bytes 0-127/{len(expected_bytes)}"
                )
                safe_full = status == 200 and body == expected_bytes
                ok = (
                    (partial or safe_full)
                    and final_url == cdn_url
                    and headers.get("x-zed-edge") == "cdn"
                    and headers.get("x-zed-source") == "github-release"
                )
                return {
                    "ok": ok,
                    "status": status,
                    "mode": "partial" if partial else "safe-full-object" if safe_full else "invalid",
                    "final_url": final_url,
                    "headers": proof_headers(headers),
                    "bytes": len(body),
                }

            record(f"github_release_{version}", True, github_release)
            record(f"direct_github_bytes_{version}", True, direct_release)
            record(f"cloudflare_cdn_get_{version}", True, cloudflare_get)
            record(f"cloudflare_cdn_head_{version}", True, cloudflare_head)
            record(f"cloudflare_cdn_range_{version}", True, cloudflare_range)

        negative = config["negative"]
        missing_url = (
            f"https://cdn.zpkg.net/github/{repo['org']}/{repo['repo']}/"
            f"{negative['missing_tag']}/{negative['missing_asset']}"
        )

        def missing_asset() -> dict[str, Any]:
            status, headers, body, final_url = request(
                missing_url,
                headers={"Accept": "application/octet-stream"},
                max_bytes=MAX_JSON,
            )
            return {
                "ok": status == 404 and final_url == missing_url and "location" not in headers,
                "status": status,
                "final_url": final_url,
                "headers": proof_headers(headers),
                "body_preview": body.decode("utf-8", errors="replace")[:300],
            }

        def malformed_path() -> dict[str, Any]:
            url = (
                f"https://cdn.zpkg.net/github/{repo['org']}/{repo['repo']}/"
                "v0.0.2/%252e%252e%252fprivate"
            )
            status, headers, body, final_url = request(url, max_bytes=MAX_JSON)
            return {
                "ok": status in (400, 404) and status != 200 and "location" not in headers,
                "status": status,
                "final_url": final_url,
                "headers": proof_headers(headers),
                "body_preview": body.decode("utf-8", errors="replace")[:300],
            }

        def public_site() -> dict[str, Any]:
            status, headers, body, final_url = request(
                "https://zpkg.net/",
                headers={"Accept": "text/html"},
                max_bytes=MAX_HTML,
            )
            html = body.decode("utf-8", errors="replace")
            return {
                "ok": status == 200 and "https://github.com/zed-pkg" in html and "Zed" in html,
                "status": status,
                "final_url": final_url,
                "headers": proof_headers(headers),
                "github_org_link_present": "https://github.com/zed-pkg" in html,
            }

        latest = canaries[-1]
        registry_url = (
            f"https://registry.zpkg.net/v1/packages/{repo['org']}/{repo['package']}/"
            f"versions/{latest['version']}"
        )

        def live_registry() -> dict[str, Any]:
            status, headers, body, final_url = request(
                registry_url,
                headers={"Accept": "application/json"},
                max_bytes=MAX_JSON,
            )
            metadata = None
            try:
                metadata = json_body(body, "live registry")
            except AssertionError:
                metadata = None
            ok = (
                status == 200
                and headers.get("x-zed-edge") == "registry"
                and headers.get("x-zed-source") == "github-public"
                and isinstance(metadata, dict)
                and metadata.get("sha256") == latest["sha256"]
                and metadata.get("size") == latest["bytes"]
            )
            return {
                "ok": ok,
                "enforced": enforce_registry,
                "status": status,
                "final_url": final_url,
                "headers": proof_headers(headers),
                "metadata": metadata,
                "body_preview": None if metadata is not None else body.decode("utf-8", errors="replace")[:500],
            }

        record("cloudflare_missing_asset_fails_closed", True, missing_asset)
        record("cloudflare_malformed_path_fails_closed", True, malformed_path)
        record("public_site_github_pages", True, public_site)
        record("live_registry_github_fallback", enforce_registry, live_registry)

    if args.zed:
        zed = args.zed.resolve()
        if not zed.is_file():
            raise AssertionError(f"zed binary not found: {zed}")
        cli_canary = next((item for item in canaries if item.get("cli_payload")), None)
        if cli_canary is None:
            raise AssertionError("canary ledger has no cli_payload fixture")
        release_base = (
            f"https://github.com/{repo['org']}/{repo['repo']}/releases/download/"
            f"{cli_canary['tag']}"
        )
        status, _headers, body, _final_url = request(
            f"{release_base}/{cli_canary['sidecar']}",
            headers={"Accept": "application/json"},
            max_bytes=MAX_JSON,
        )
        if status != 200:
            raise AssertionError(f"CLI sidecar returned HTTP {status}")
        sidecar = json_body(body, "CLI sidecar")

        def cli_install() -> dict[str, Any]:
            work = args.work_root.resolve()
            if work.exists():
                raise AssertionError(f"work root must be fresh: {work}")
            work.mkdir(parents=True)
            zed_frozen_install(zed, work, sidecar)
            payloads = sorted(work.rglob("payload.txt"))
            expected_payload = cli_canary["cli_payload"]
            ok = bool(payloads) and all(
                payload.read_text(encoding="utf-8") == expected_payload for payload in payloads
            )
            return {
                "ok": ok,
                "zed": str(zed),
                "payload_files": [str(path.relative_to(work)) for path in payloads],
                "sha256": sidecar.get("sha256"),
                "registry_mode": "unreachable-loopback",
                "frozen_reinstall": True,
            }

        record("zed_cli_install_with_registry_down", True, cli_install)
    elif args.cli_only:
        raise AssertionError("--cli-only requires --zed")

    evidence["summary"] = {
        "required_failures": failures,
        "all_required_checks_passed": not failures,
        "live_registry_enforced": enforce_registry,
        "mode": "cli-only" if args.cli_only else "edge",
    }
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2, sort_keys=True), flush=True)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        rows = ["# Zed public fallback attestation", "", "| Check | Required | Result |", "|---|---:|---:|"]
        for name, result in evidence["checks"].items():
            rows.append(
                f"| `{name}` | {'yes' if result.get('required') else 'no'} | "
                f"{'PASS' if result.get('ok') else 'FAIL'} |"
            )
        rows.extend(["", f"Required failures: **{len(failures)}**", ""])
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(rows))

    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001
        print(f"public fallback attestation failed: {error}", file=sys.stderr)
        raise
