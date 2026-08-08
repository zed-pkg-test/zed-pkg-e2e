#!/usr/bin/env python3
"""Fail-closed inventory checks for every repository in the zed-pkg-test org.

The static lane proves the checked-in inventory, lifecycle matrix, and lifecycle
package-source map agree. The live lane queries GitHub with read-only access and
proves that every public fixture repository still exists with the expected
shape and a parseable root manifest.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
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
from types import ModuleType
from typing import Any, Callable, Iterable, Mapping, Sequence

INVENTORY_SCHEMA = "zed-pkg-test/repository-inventory-v1"
VALID_KINDS = {"package", "orchestrator"}
REPOSITORY_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
API_VERSION = "2022-11-28"


class InventoryError(ValueError):
    """Raised when checked-in or live fixture inventory is inconsistent."""


@dataclass(frozen=True)
class RepositorySpec:
    name: str
    kind: str
    manifest: str | None

    @property
    def is_package(self) -> bool:
        return self.kind == "package"


@dataclass(frozen=True)
class Inventory:
    organization: str
    orchestrator: str
    repositories: tuple[RepositorySpec, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(repo.name for repo in self.repositories)

    @property
    def package_names(self) -> tuple[str, ...]:
        return tuple(repo.name for repo in self.repositories if repo.is_package)

    def by_name(self) -> dict[str, RepositorySpec]:
        return {repo.name: repo for repo in self.repositories}


@dataclass(frozen=True)
class LiveRepository:
    name: str
    private: bool
    archived: bool
    disabled: bool
    fork: bool
    default_branch: str


@dataclass(frozen=True)
class ManifestProbe:
    repository: str
    path: str
    present: bool
    package: str | None
    version: str | None


JsonOpener = Callable[[urllib.request.Request], Any]


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InventoryError(f"{field} must be a non-empty string")
    return value


def load_inventory(path: Path) -> Inventory:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InventoryError(f"cannot read inventory {path}: {error}") from error
    if not isinstance(raw, dict):
        raise InventoryError("inventory root must be an object")
    if raw.get("schema") != INVENTORY_SCHEMA:
        raise InventoryError(
            f"inventory schema must be {INVENTORY_SCHEMA!r}, got {raw.get('schema')!r}"
        )

    organization = _require_string(raw.get("organization"), "organization")
    orchestrator = _require_string(raw.get("orchestrator"), "orchestrator")
    entries = raw.get("repositories")
    if not isinstance(entries, list) or not entries:
        raise InventoryError("repositories must be a non-empty array")

    repositories: list[RepositorySpec] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise InventoryError(f"repositories[{index}] must be an object")
        name = _require_string(entry.get("name"), f"repositories[{index}].name")
        if not REPOSITORY_NAME_RE.fullmatch(name):
            raise InventoryError(f"invalid repository name: {name!r}")
        if name in seen:
            raise InventoryError(f"duplicate repository: {name}")
        seen.add(name)

        kind = _require_string(entry.get("kind"), f"repositories[{index}].kind")
        if kind not in VALID_KINDS:
            raise InventoryError(f"{name}: invalid kind {kind!r}")

        manifest = entry.get("manifest")
        if kind == "package":
            manifest = _require_string(manifest, f"{name}.manifest")
            manifest_path = Path(manifest)
            if manifest_path.is_absolute() or ".." in manifest_path.parts:
                raise InventoryError(f"{name}: manifest path escapes repository")
        elif manifest is not None:
            raise InventoryError(f"{name}: orchestrator manifest must be null")

        repositories.append(
            RepositorySpec(name=name, kind=kind, manifest=manifest)
        )

    orchestrators = [repo.name for repo in repositories if repo.kind == "orchestrator"]
    if orchestrators != [orchestrator]:
        raise InventoryError(
            "inventory must contain exactly the declared orchestrator; "
            f"declared={orchestrator!r}, classified={orchestrators!r}"
        )

    return Inventory(
        organization=organization,
        orchestrator=orchestrator,
        repositories=tuple(repositories),
    )


def extract_lifecycle_repositories(workflow_text: str) -> tuple[str, ...]:
    """Extract the `strategy.matrix.repo` sequence from lifecycle.yml.

    This deliberately supports only the narrow checked-in shape. A workflow
    refactor that makes coverage ambiguous must update this parser and tests
    rather than silently skipping repositories.
    """

    lines = workflow_text.splitlines()
    matrix_indent: int | None = None
    repo_indent: int | None = None
    repositories: list[str] = []

    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if stripped == "matrix:":
            matrix_indent = indent
            repo_indent = None
            repositories = []
            continue
        if matrix_indent is None:
            continue
        if stripped and indent <= matrix_indent:
            matrix_indent = None
            repo_indent = None
            continue
        if stripped == "repo:" and indent > matrix_indent:
            repo_indent = indent
            continue
        if repo_indent is None:
            continue
        if stripped and indent <= repo_indent:
            break
        match = re.fullmatch(r"-\s+([A-Za-z0-9_.-]+)", stripped)
        if match:
            repositories.append(match.group(1))
        elif stripped and not stripped.startswith("#"):
            raise InventoryError(
                f"unsupported lifecycle repo matrix entry: {stripped!r}"
            )

    if not repositories:
        raise InventoryError("cannot find strategy.matrix.repo in lifecycle workflow")
    if len(repositories) != len(set(repositories)):
        raise InventoryError("lifecycle repo matrix contains duplicates")
    return tuple(repositories)


def load_lifecycle_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("zed_inventory_lifecycle", path)
    if spec is None or spec.loader is None:
        raise InventoryError(f"cannot load lifecycle module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def validate_static_inventory(
    inventory: Inventory,
    lifecycle_repositories: Sequence[str],
    package_sources: Mapping[str, tuple[str, str]],
    non_package_repositories: Iterable[str],
) -> list[str]:
    errors: list[str] = []
    expected = set(inventory.names)
    matrix = set(lifecycle_repositories)

    missing_from_matrix = sorted(expected - matrix)
    extra_in_matrix = sorted(matrix - expected)
    if missing_from_matrix:
        errors.append(
            "repositories missing from lifecycle matrix: "
            + ", ".join(missing_from_matrix)
        )
    if extra_in_matrix:
        errors.append(
            "unknown repositories in lifecycle matrix: " + ", ".join(extra_in_matrix)
        )

    expected_non_packages = {inventory.orchestrator}
    actual_non_packages = set(non_package_repositories)
    if actual_non_packages != expected_non_packages:
        errors.append(
            "NON_PACKAGE_REPOS drift: "
            f"expected={sorted(expected_non_packages)!r}, "
            f"actual={sorted(actual_non_packages)!r}"
        )

    package_repositories = set(inventory.package_names)
    source_repositories = {repo for repo, _relative in package_sources.values()}
    unknown_source_repositories = sorted(source_repositories - package_repositories)
    if unknown_source_repositories:
        errors.append(
            "PACKAGE_SOURCES references unclassified repositories: "
            + ", ".join(unknown_source_repositories)
        )

    for package, source in package_sources.items():
        if not re.fullmatch(r"[^/\s]+/[^/\s]+", package):
            errors.append(f"invalid PACKAGE_SOURCES package identity: {package!r}")
        if not isinstance(source, tuple) or len(source) != 2:
            errors.append(f"{package}: source must be a (repository, path) tuple")
            continue
        repo, relative = source
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            errors.append(f"{package}: source path escapes repository: {relative!r}")
        if repo == inventory.orchestrator:
            errors.append(f"{package}: orchestrator cannot be a package source")

    return errors


def _headers(token: str | None) -> dict[str, str]:
    result = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "zed-pkg-test-inventory/1",
    }
    if token:
        result["Authorization"] = f"Bearer {token}"
    return result


def fetch_json(
    url: str,
    *,
    token: str | None = None,
    opener: JsonOpener = urllib.request.urlopen,
) -> Any:
    request = urllib.request.Request(url, headers=_headers(token))
    try:
        with opener(request) as response:
            payload = response.read()
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise InventoryError(
            f"GitHub API request failed ({error.code}) for {url}: {body[:300]}"
        ) from error
    except urllib.error.URLError as error:
        raise InventoryError(f"GitHub API request failed for {url}: {error}") from error
    try:
        return json.loads(payload)
    except json.JSONDecodeError as error:
        raise InventoryError(f"GitHub API returned invalid JSON for {url}") from error


def fetch_live_repositories(
    organization: str,
    *,
    token: str | None = None,
    opener: JsonOpener = urllib.request.urlopen,
) -> tuple[LiveRepository, ...]:
    encoded = urllib.parse.quote(organization, safe="")
    url = (
        f"https://api.github.com/orgs/{encoded}/repos"
        "?type=public&per_page=100&sort=full_name&direction=asc"
    )
    payload = fetch_json(url, token=token, opener=opener)
    if not isinstance(payload, list):
        raise InventoryError("organization repositories response must be an array")

    repositories: list[LiveRepository] = []
    for item in payload:
        if not isinstance(item, dict):
            raise InventoryError("organization repository entry must be an object")
        repositories.append(
            LiveRepository(
                name=_require_string(item.get("name"), "live repository name"),
                private=bool(item.get("private")),
                archived=bool(item.get("archived")),
                disabled=bool(item.get("disabled")),
                fork=bool(item.get("fork")),
                default_branch=_require_string(
                    item.get("default_branch"), "live repository default_branch"
                ),
            )
        )
    return tuple(repositories)


def validate_live_repositories(
    inventory: Inventory, live_repositories: Sequence[LiveRepository]
) -> list[str]:
    errors: list[str] = []
    expected = set(inventory.names)
    live_names = {repo.name for repo in live_repositories}

    missing = sorted(expected - live_names)
    unexpected = sorted(live_names - expected)
    if missing:
        errors.append("live repositories missing from org: " + ", ".join(missing))
    if unexpected:
        errors.append(
            "unclassified live repositories in org: " + ", ".join(unexpected)
        )

    for repo in live_repositories:
        if repo.name not in expected:
            continue
        if repo.private:
            errors.append(f"{repo.name}: fixture repository must be public")
        if repo.archived:
            errors.append(f"{repo.name}: fixture repository must not be archived")
        if repo.disabled:
            errors.append(f"{repo.name}: fixture repository must not be disabled")
        if repo.fork:
            errors.append(f"{repo.name}: fixture repository must not be a fork")
        if repo.default_branch != "main":
            errors.append(
                f"{repo.name}: default branch must be main, got {repo.default_branch!r}"
            )
    return errors


def decode_manifest_payload(repository: str, path: str, payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise InventoryError(f"{repository}:{path}: contents response must be an object")
    if payload.get("type") != "file":
        raise InventoryError(f"{repository}:{path}: manifest is not a regular file")
    if payload.get("encoding") != "base64":
        raise InventoryError(
            f"{repository}:{path}: expected base64 contents encoding"
        )
    content = payload.get("content")
    if not isinstance(content, str):
        raise InventoryError(f"{repository}:{path}: manifest content is missing")
    try:
        decoded = base64.b64decode(content, validate=False)
        manifest = tomllib.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise InventoryError(f"{repository}:{path}: invalid TOML: {error}") from error
    if not isinstance(manifest, dict):
        raise InventoryError(f"{repository}:{path}: manifest root must be a table")
    package = manifest.get("package")
    if not isinstance(package, dict):
        raise InventoryError(f"{repository}:{path}: missing [package] table")
    for field in ("org", "name", "version"):
        _require_string(package.get(field), f"{repository}:{path}: package.{field}")
    return manifest


def fetch_manifest_probe(
    organization: str,
    spec: RepositorySpec,
    *,
    token: str | None = None,
    opener: JsonOpener = urllib.request.urlopen,
) -> ManifestProbe:
    if spec.manifest is None:
        return ManifestProbe(
            repository=spec.name,
            path="",
            present=False,
            package=None,
            version=None,
        )

    org = urllib.parse.quote(organization, safe="")
    repo = urllib.parse.quote(spec.name, safe="")
    path = urllib.parse.quote(spec.manifest, safe="/")
    url = f"https://api.github.com/repos/{org}/{repo}/contents/{path}?ref=main"
    payload = fetch_json(url, token=token, opener=opener)
    manifest = decode_manifest_payload(spec.name, spec.manifest, payload)
    package = manifest["package"]
    return ManifestProbe(
        repository=spec.name,
        path=spec.manifest,
        present=True,
        package=f"{package['org']}/{package['name']}",
        version=package["version"],
    )


def build_report(
    inventory: Inventory,
    lifecycle_repositories: Sequence[str],
    live_repositories: Sequence[LiveRepository] | None,
    manifest_probes: Sequence[ManifestProbe],
) -> dict[str, Any]:
    return {
        "schema": INVENTORY_SCHEMA,
        "organization": inventory.organization,
        "orchestrator": inventory.orchestrator,
        "inventory_repository_count": len(inventory.repositories),
        "package_repository_count": len(inventory.package_names),
        "lifecycle_repository_count": len(lifecycle_repositories),
        "live_repository_count": (
            len(live_repositories) if live_repositories is not None else None
        ),
        "repositories": [asdict(repo) for repo in inventory.repositories],
        "manifests": [asdict(probe) for probe in manifest_probes],
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    inventory = load_inventory(args.inventory)
    workflow_text = args.workflow.read_text(encoding="utf-8")
    lifecycle_repositories = extract_lifecycle_repositories(workflow_text)
    lifecycle = load_lifecycle_module(args.lifecycle)

    static_errors = validate_static_inventory(
        inventory,
        lifecycle_repositories,
        lifecycle.PACKAGE_SOURCES,
        lifecycle.NON_PACKAGE_REPOS,
    )
    if static_errors:
        raise InventoryError("\n".join(static_errors))

    live_repositories: tuple[LiveRepository, ...] | None = None
    manifest_probes: list[ManifestProbe] = []
    if args.live:
        token = os.environ.get("GITHUB_TOKEN") or None
        live_repositories = fetch_live_repositories(
            inventory.organization,
            token=token,
        )
        live_errors = validate_live_repositories(inventory, live_repositories)
        if live_errors:
            raise InventoryError("\n".join(live_errors))

        for spec in inventory.repositories:
            if spec.is_package:
                manifest_probes.append(
                    fetch_manifest_probe(
                        inventory.organization,
                        spec,
                        token=token,
                    )
                )
            else:
                manifest_probes.append(
                    ManifestProbe(
                        repository=spec.name,
                        path="",
                        present=False,
                        package=None,
                        version=None,
                    )
                )

    report = build_report(
        inventory,
        lifecycle_repositories,
        live_repositories,
        manifest_probes,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("fixtures/org-repositories.json"),
    )
    parser.add_argument(
        "--workflow",
        type=Path,
        default=Path(".github/workflows/lifecycle.yml"),
    )
    parser.add_argument(
        "--lifecycle",
        type=Path,
        default=Path("scripts/lifecycle.py"),
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="query the public GitHub organization and validate root manifests",
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = run(args)
    except (InventoryError, OSError, AttributeError) as error:
        print(f"inventory check failed: {error}", file=sys.stderr)
        return 1
    print(
        "inventory check passed: "
        f"{report['inventory_repository_count']} repositories, "
        f"{report['package_repository_count']} package fixtures, "
        f"{report['lifecycle_repository_count']} lifecycle lanes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
