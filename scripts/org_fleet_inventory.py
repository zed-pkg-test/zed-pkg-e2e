#!/usr/bin/env python3
"""Audit the complete public zed-pkg-test repository fleet.

The checked-in core inventory remains the reviewed lifecycle contract. This
module adds a second, growth-safe live audit that discovers every public repo,
classifies supplemental package fixtures versus harness/governance repos, and
validates all required or present root Zed package manifests.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

CORE_SCHEMA = "zed-pkg-test/repository-inventory-v1"
REPORT_SCHEMA = "zed-pkg-test/public-fleet-inventory-v2"
API_VERSION = "2022-11-28"
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
VALID_CORE_KINDS = {"package", "orchestrator"}


class FleetError(ValueError):
    """Raised when the inventory or a GitHub response is malformed."""


@dataclass(frozen=True)
class CoreSpec:
    name: str
    kind: str
    manifest: str | None

    @property
    def requires_manifest(self) -> bool:
        return self.kind == "package"


@dataclass(frozen=True)
class LiveRepository:
    name: str
    private: bool = False
    archived: bool = False
    disabled: bool = False
    fork: bool = False
    default_branch: str = "main"


@dataclass(frozen=True)
class ManifestProbe:
    repository: str
    path: str
    present: bool
    package_org: str | None = None
    package_name: str | None = None
    version: str | None = None


ManifestLoader = Callable[[LiveRepository, str], ManifestProbe]
UrlOpener = Callable[[urllib.request.Request], Any]


def _non_empty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FleetError(f"{field} must be a non-empty string")
    return value


def load_core_inventory(path: Path) -> tuple[str, dict[str, CoreSpec]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FleetError(f"cannot read core inventory {path}: {error}") from error
    if not isinstance(raw, dict):
        raise FleetError("core inventory root must be an object")
    if raw.get("schema") != CORE_SCHEMA:
        raise FleetError(
            f"core inventory schema must be {CORE_SCHEMA!r}, got {raw.get('schema')!r}"
        )
    organization = _non_empty_string(raw.get("organization"), "organization")
    orchestrator = _non_empty_string(raw.get("orchestrator"), "orchestrator")
    entries = raw.get("repositories")
    if not isinstance(entries, list) or not entries:
        raise FleetError("repositories must be a non-empty array")

    result: dict[str, CoreSpec] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise FleetError(f"repositories[{index}] must be an object")
        name = _non_empty_string(entry.get("name"), f"repositories[{index}].name")
        if not NAME_RE.fullmatch(name):
            raise FleetError(f"invalid repository name: {name!r}")
        if name in result:
            raise FleetError(f"duplicate core repository: {name}")
        kind = _non_empty_string(entry.get("kind"), f"{name}.kind")
        if kind not in VALID_CORE_KINDS:
            raise FleetError(f"{name}: invalid core kind {kind!r}")
        manifest = entry.get("manifest")
        if kind == "package":
            manifest = _non_empty_string(manifest, f"{name}.manifest")
            manifest_path = Path(manifest)
            if manifest_path.is_absolute() or ".." in manifest_path.parts:
                raise FleetError(f"{name}: manifest path escapes repository")
        elif manifest is not None:
            raise FleetError(f"{name}: orchestrator manifest must be null")
        result[name] = CoreSpec(name=name, kind=kind, manifest=manifest)

    orchestrators = [item.name for item in result.values() if item.kind == "orchestrator"]
    if orchestrators != [orchestrator]:
        raise FleetError(
            "core inventory must contain exactly the declared orchestrator; "
            f"declared={orchestrator!r}, classified={orchestrators!r}"
        )
    return organization, result


def classify_repository(name: str, core: Mapping[str, CoreSpec]) -> str:
    spec = core.get(name)
    if spec is not None:
        return f"core-{spec.kind}"
    if name == ".github":
        return "governance"
    if name.endswith("-e2e") or name.endswith("-contract"):
        return "harness"
    return "supplemental-package"


def manifest_requirement(
    repository: LiveRepository,
    core: Mapping[str, CoreSpec],
) -> tuple[str, bool]:
    spec = core.get(repository.name)
    if spec is not None:
        return spec.manifest or ".zpkg.toml", spec.requires_manifest
    category = classify_repository(repository.name, core)
    return ".zpkg.toml", category == "supplemental-package"


def audit_fleet(
    organization: str,
    core: Mapping[str, CoreSpec],
    live_repositories: Sequence[LiveRepository],
    load_manifest: ManifestLoader,
) -> dict[str, Any]:
    errors: list[str] = []
    rows: list[dict[str, Any]] = []

    live_names = [repo.name for repo in live_repositories]
    duplicate_names = sorted({name for name in live_names if live_names.count(name) > 1})
    if duplicate_names:
        errors.append("duplicate live repositories: " + ", ".join(duplicate_names))

    missing_core = sorted(set(core) - set(live_names))
    if missing_core:
        errors.append("core repositories missing from public org: " + ", ".join(missing_core))

    required_manifest_count = 0
    required_manifest_present_count = 0
    manifest_present_count = 0

    for repo in sorted(live_repositories, key=lambda item: item.name):
        category = classify_repository(repo.name, core)
        repo_errors: list[str] = []
        if repo.private:
            repo_errors.append("repository must be public")
        if repo.archived:
            repo_errors.append("repository must not be archived")
        if repo.disabled:
            repo_errors.append("repository must not be disabled")
        if repo.fork:
            repo_errors.append("repository must not be a fork")
        if repo.default_branch != "main":
            repo_errors.append(
                f"default branch must be main, got {repo.default_branch!r}"
            )

        manifest_path, manifest_required = manifest_requirement(repo, core)
        if manifest_required:
            required_manifest_count += 1
        try:
            probe = load_manifest(repo, manifest_path)
        except FleetError as error:
            probe = ManifestProbe(repo.name, manifest_path, False)
            repo_errors.append(str(error))

        if manifest_required and not probe.present:
            repo_errors.append(f"required manifest is missing: {manifest_path}")
        if probe.present:
            manifest_present_count += 1
            if manifest_required:
                required_manifest_present_count += 1
            if probe.package_org != organization:
                repo_errors.append(
                    "manifest package.org mismatch: "
                    f"expected {organization!r}, got {probe.package_org!r}"
                )
            if probe.package_name != repo.name:
                repo_errors.append(
                    "manifest package.name mismatch: "
                    f"expected {repo.name!r}, got {probe.package_name!r}"
                )

        for message in repo_errors:
            errors.append(f"{repo.name}: {message}")
        rows.append(
            {
                "name": repo.name,
                "category": category,
                "core": repo.name in core,
                "private": repo.private,
                "archived": repo.archived,
                "disabled": repo.disabled,
                "fork": repo.fork,
                "default_branch": repo.default_branch,
                "manifest_required": manifest_required,
                "manifest": asdict(probe),
                "errors": repo_errors,
            }
        )

    category_counts: dict[str, int] = {}
    for row in rows:
        category = str(row["category"])
        category_counts[category] = category_counts.get(category, 0) + 1

    return {
        "schema": REPORT_SCHEMA,
        "organization": organization,
        "core_repository_count": len(core),
        "core_package_count": sum(spec.requires_manifest for spec in core.values()),
        "live_repository_count": len(live_repositories),
        "supplemental_repository_count": len(set(live_names) - set(core)),
        "missing_core_repositories": missing_core,
        "category_counts": dict(sorted(category_counts.items())),
        "required_manifest_count": required_manifest_count,
        "required_manifest_present_count": required_manifest_present_count,
        "manifest_present_count": manifest_present_count,
        "repositories": rows,
        "errors": errors,
    }


def _headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "zed-pkg-test-fleet-inventory/2",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _read_json_response(
    request: urllib.request.Request,
    *,
    opener: UrlOpener,
    allow_not_found: bool = False,
) -> tuple[Any | None, Mapping[str, str]]:
    try:
        with opener(request) as response:
            payload = response.read()
            headers = response.headers
    except urllib.error.HTTPError as error:
        if allow_not_found and error.code == 404:
            return None, {}
        body = error.read().decode("utf-8", errors="replace")
        raise FleetError(
            f"GitHub API request failed ({error.code}) for {request.full_url}: {body[:300]}"
        ) from error
    except urllib.error.URLError as error:
        raise FleetError(
            f"GitHub API request failed for {request.full_url}: {error}"
        ) from error
    try:
        return json.loads(payload), headers
    except json.JSONDecodeError as error:
        raise FleetError(
            f"GitHub API returned invalid JSON for {request.full_url}"
        ) from error


def fetch_public_repositories(
    organization: str,
    *,
    token: str | None = None,
    opener: UrlOpener = urllib.request.urlopen,
) -> tuple[LiveRepository, ...]:
    encoded = urllib.parse.quote(organization, safe="")
    repositories: list[LiveRepository] = []
    page = 1
    while True:
        url = (
            f"https://api.github.com/orgs/{encoded}/repos"
            f"?type=public&per_page=100&page={page}&sort=full_name&direction=asc"
        )
        request = urllib.request.Request(url, headers=_headers(token))
        payload, _headers_map = _read_json_response(request, opener=opener)
        if not isinstance(payload, list):
            raise FleetError("organization repositories response must be an array")
        for item in payload:
            if not isinstance(item, dict):
                raise FleetError("organization repository entry must be an object")
            repositories.append(
                LiveRepository(
                    name=_non_empty_string(item.get("name"), "live repository name"),
                    private=bool(item.get("private")),
                    archived=bool(item.get("archived")),
                    disabled=bool(item.get("disabled")),
                    fork=bool(item.get("fork")),
                    default_branch=_non_empty_string(
                        item.get("default_branch"), "live repository default_branch"
                    ),
                )
            )
        if len(payload) < 100:
            break
        page += 1
    return tuple(repositories)


def decode_manifest(
    repository: str,
    path: str,
    payload: object,
) -> ManifestProbe:
    if not isinstance(payload, dict):
        raise FleetError(f"{path}: contents response must be an object")
    if payload.get("type") != "file":
        raise FleetError(f"{path}: manifest is not a regular file")
    if payload.get("encoding") != "base64":
        raise FleetError(f"{path}: expected base64 contents encoding")
    content = payload.get("content")
    if not isinstance(content, str):
        raise FleetError(f"{path}: manifest content is missing")
    try:
        decoded = base64.b64decode(content, validate=False).decode("utf-8")
        manifest = tomllib.loads(decoded)
    except (ValueError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise FleetError(f"{path}: invalid TOML: {error}") from error
    package = manifest.get("package") if isinstance(manifest, dict) else None
    if not isinstance(package, dict):
        raise FleetError(f"{path}: missing [package] table")
    package_org = _non_empty_string(package.get("org"), f"{path}: package.org")
    package_name = _non_empty_string(package.get("name"), f"{path}: package.name")
    version = _non_empty_string(package.get("version"), f"{path}: package.version")
    return ManifestProbe(
        repository=repository,
        path=path,
        present=True,
        package_org=package_org,
        package_name=package_name,
        version=version,
    )


def make_github_manifest_loader(
    organization: str,
    *,
    token: str | None = None,
    opener: UrlOpener = urllib.request.urlopen,
) -> ManifestLoader:
    encoded_org = urllib.parse.quote(organization, safe="")

    def load(repository: LiveRepository, path: str) -> ManifestProbe:
        encoded_repo = urllib.parse.quote(repository.name, safe="")
        encoded_path = urllib.parse.quote(path, safe="/")
        url = (
            f"https://api.github.com/repos/{encoded_org}/{encoded_repo}/contents/"
            f"{encoded_path}?ref=main"
        )
        request = urllib.request.Request(url, headers=_headers(token))
        payload, _response_headers = _read_json_response(
            request,
            opener=opener,
            allow_not_found=True,
        )
        if payload is None:
            return ManifestProbe(repository.name, path, False)
        return decode_manifest(repository.name, path, payload)

    return load


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("fixtures/org-repositories.json"),
        help="reviewed core lifecycle inventory",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--token-env",
        default="GITHUB_TOKEN",
        help="environment variable containing a read-only GitHub token",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        organization, core = load_core_inventory(args.inventory)
        token = os.environ.get(args.token_env) or None
        live = fetch_public_repositories(organization, token=token)
        loader = make_github_manifest_loader(organization, token=token)
        report = audit_fleet(organization, core, live, loader)
    except FleetError as error:
        print(f"fleet inventory failed: {error}", file=sys.stderr)
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if report["errors"]:
        print("fleet inventory failed:", file=sys.stderr)
        for error in report["errors"]:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(
        "fleet inventory passed: "
        f"{report['live_repository_count']} public repositories, "
        f"{report['manifest_present_count']} valid manifests, "
        f"{report['supplemental_repository_count']} supplemental repositories"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
