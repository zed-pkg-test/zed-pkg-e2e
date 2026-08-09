#!/usr/bin/env python3
"""Resolve immutable, tag-ready Zed package candidates from GitHub.

The resulting JSON is the release ledger input. Every selected package has a
root `.zpkg.toml`, a valid `zed-pkg/<name>@<version>` identity, and a matching
release tag that peels to the exact default-branch head recorded in the ledger.
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
from collections import defaultdict, deque
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,127}$")


class InventoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class Candidate:
    repo: str
    sha: str
    package: str
    org: str
    name: str
    version: str
    tag: str
    tag_sha: str
    archive_format: str
    repository_url: str
    dependencies: tuple[str, ...]


def api_get(path: str, token: str | None) -> dict[str, Any]:
    url = path if path.startswith("https://") else f"https://api.github.com{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "zed-pkg-production-inventory/1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise InventoryError(f"GitHub API {error.code} for {url}: {body[:500]}") from error
    except urllib.error.URLError as error:
        raise InventoryError(f"GitHub API unavailable for {url}: {error}") from error


def decode_manifest(payload: dict[str, Any], coordinate: str) -> dict[str, Any]:
    if payload.get("type") != "file" or payload.get("encoding") != "base64":
        raise InventoryError(f"{coordinate}: root .zpkg.toml is not a base64 file")
    try:
        raw = base64.b64decode(payload["content"], validate=False)
        document = tomllib.loads(raw.decode("utf-8"))
    except (KeyError, ValueError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise InventoryError(f"{coordinate}: invalid .zpkg.toml: {error}") from error
    if not isinstance(document, dict):
        raise InventoryError(f"{coordinate}: manifest root is not a table")
    return document


def peel_tag(org: str, repo: str, tag: str, token: str | None) -> str:
    encoded = urllib.parse.quote(tag, safe="")
    reference = api_get(f"/repos/{org}/{repo}/git/ref/tags/{encoded}", token)
    obj = reference.get("object") or {}
    sha = obj.get("sha")
    kind = obj.get("type")
    for _ in range(8):
        if kind == "commit" and isinstance(sha, str) and SHA_RE.fullmatch(sha):
            return sha
        if kind != "tag" or not isinstance(sha, str):
            break
        annotation = api_get(f"/repos/{org}/{repo}/git/tags/{sha}", token)
        obj = annotation.get("object") or {}
        sha = obj.get("sha")
        kind = obj.get("type")
    raise InventoryError(f"{org}/{repo}: tag {tag!r} does not peel to a commit")


def archive_format(document: dict[str, Any]) -> str:
    publish = document.get("publish")
    value: Any = None
    if isinstance(publish, dict):
        value = publish.get("format") or publish.get("archive_format")
    if value is None:
        value = "tar.gz"
    aliases = {"tgz": "tar.gz", "tar_gz": "tar.gz", "tar_zst": "tar.zst"}
    value = aliases.get(str(value), str(value))
    if value not in {"tar.gz", "tar.zst", "zip"}:
        raise InventoryError(f"unsupported archive format {value!r}")
    return value


def parse_candidate(
    org: str,
    repo: str,
    sha: str,
    document: dict[str, Any],
    token: str | None,
) -> Candidate:
    package = document.get("package")
    if not isinstance(package, dict):
        raise InventoryError(f"{org}/{repo}: missing [package]")
    manifest_org = package.get("org")
    name = package.get("name")
    version = package.get("version")
    if manifest_org != org:
        raise InventoryError(f"{org}/{repo}: package.org is {manifest_org!r}, expected {org!r}")
    if not isinstance(name, str) or not name or len(name) > 128:
        raise InventoryError(f"{org}/{repo}: invalid package.name")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise InventoryError(f"{org}/{repo}: invalid package.version {version!r}")
    repository = package.get("repository")
    if not isinstance(repository, dict) or not isinstance(repository.get("url"), str):
        raise InventoryError(f"{org}/{repo}: missing [package.repository].url")
    expected_url = f"https://github.com/{org}/{repo}"
    actual_url = repository["url"].removesuffix(".git").rstrip("/")
    if actual_url.casefold() != expected_url.casefold():
        raise InventoryError(
            f"{org}/{repo}: repository URL {actual_url!r} does not match {expected_url!r}"
        )
    publish = document.get("publish")
    tag_format = publish.get("tag_format", "v{version}") if isinstance(publish, dict) else "v{version}"
    if not isinstance(tag_format, str) or tag_format.count("{version}") != 1:
        raise InventoryError(f"{org}/{repo}: publish.tag_format must contain exactly one {{version}}")
    tag = tag_format.replace("{version}", version)
    tag_sha = peel_tag(org, repo, tag, token)
    if tag_sha != sha:
        raise InventoryError(
            f"{org}/{repo}: tag {tag!r} peels to {tag_sha}, default-branch head is {sha}"
        )
    dependencies = document.get("dependencies")
    dependency_names: tuple[str, ...]
    if dependencies is None:
        dependency_names = ()
    elif isinstance(dependencies, dict) and all(isinstance(key, str) for key in dependencies):
        dependency_names = tuple(sorted(dependencies))
    else:
        raise InventoryError(f"{org}/{repo}: [dependencies] is not a string-keyed table")
    return Candidate(
        repo=repo,
        sha=sha,
        package=f"{manifest_org}/{name}",
        org=manifest_org,
        name=name,
        version=version,
        tag=tag,
        tag_sha=tag_sha,
        archive_format=archive_format(document),
        repository_url=expected_url,
        dependencies=dependency_names,
    )


def topological_order(candidates: list[Candidate]) -> list[Candidate]:
    by_package = {candidate.package: candidate for candidate in candidates}
    incoming = {candidate.package: 0 for candidate in candidates}
    outgoing: dict[str, set[str]] = defaultdict(set)
    for candidate in candidates:
        for dependency in candidate.dependencies:
            if dependency not in by_package:
                continue
            if candidate.package not in outgoing[dependency]:
                outgoing[dependency].add(candidate.package)
                incoming[candidate.package] += 1
    queue = deque(sorted(package for package, degree in incoming.items() if degree == 0))
    ordered: list[Candidate] = []
    while queue:
        package = queue.popleft()
        ordered.append(by_package[package])
        for dependent in sorted(outgoing[package]):
            incoming[dependent] -= 1
            if incoming[dependent] == 0:
                queue.append(dependent)
    if len(ordered) != len(candidates):
        cycle = sorted(package for package, degree in incoming.items() if degree > 0)
        raise InventoryError(f"selected package dependencies contain a cycle: {cycle}")
    return ordered


def write_outputs(candidates: list[Candidate], rejected: list[dict[str, str]], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    ledger = {
        "schema": 1,
        "count": len(candidates),
        "packages": [asdict(candidate) for candidate in candidates],
        "rejected": rejected,
    }
    (output / "production-package-inventory.json").write_text(
        json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# Production package inventory",
        "",
        f"Selected immutable packages: **{len(candidates)}**",
        "",
        "| Order | Package | Version | Repository | Commit | Tag | Format |",
        "| ---: | --- | --- | --- | --- | --- | --- |",
    ]
    for index, candidate in enumerate(candidates, 1):
        lines.append(
            f"| {index} | `{candidate.package}` | `{candidate.version}` | "
            f"`{candidate.repo}` | `{candidate.sha}` | `{candidate.tag}` | "
            f"`{candidate.archive_format}` |"
        )
    if rejected:
        lines.extend(["", "## Rejected candidates", ""])
        for item in rejected:
            lines.append(f"- `{item['repo']}` — {item['reason']}")
    (output / "production-package-inventory.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        compact = json.dumps([asdict(candidate) for candidate in candidates], separators=(",", ":"))
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(f"matrix={compact}\n")
            handle.write(f"count={len(candidates)}\n")


def inventory(config_path: Path, output: Path, token: str | None) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    org = config["organization"]
    minimum = int(config["minimum_packages"])
    maximum = int(config["maximum_packages"])
    repos = config["repositories"]
    if not isinstance(repos, list) or not repos or len(repos) != len(set(repos)):
        raise InventoryError("repository candidates must be a non-empty unique list")
    candidates: list[Candidate] = []
    rejected: list[dict[str, str]] = []
    for repo in repos:
        try:
            metadata = api_get(f"/repos/{org}/{repo}", token)
            if metadata.get("archived"):
                raise InventoryError("repository is archived")
            default_branch = metadata.get("default_branch")
            if not isinstance(default_branch, str):
                raise InventoryError("repository has no default branch")
            commit = api_get(f"/repos/{org}/{repo}/commits/{urllib.parse.quote(default_branch, safe='')}", token)
            sha = commit.get("sha")
            if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
                raise InventoryError("default branch did not resolve to an exact commit")
            manifest = api_get(f"/repos/{org}/{repo}/contents/.zpkg.toml?ref={sha}", token)
            document = decode_manifest(manifest, f"{org}/{repo}@{sha}")
            candidates.append(parse_candidate(org, repo, sha, document, token))
        except InventoryError as error:
            rejected.append({"repo": repo, "reason": str(error)})
    candidates = topological_order(candidates)
    if not minimum <= len(candidates) <= maximum:
        write_outputs(candidates, rejected, output)
        raise InventoryError(
            f"tag-ready root-manifest package count {len(candidates)} is outside [{minimum}, {maximum}]"
        )
    if not any(candidate.name == "zed-lib-core" for candidate in candidates):
        raise InventoryError("zed-lib-core is mandatory but was not selected")
    if len({candidate.package for candidate in candidates}) != len(candidates):
        raise InventoryError("selected package coordinates are not unique")
    write_outputs(candidates, rejected, output)


def self_test() -> None:
    sample = [
        Candidate("b", "b" * 40, "zed-pkg/b", "zed-pkg", "b", "1.0.0", "v1.0.0", "b" * 40, "zip", "https://github.com/zed-pkg/b", ("zed-pkg/a",)),
        Candidate("a", "a" * 40, "zed-pkg/a", "zed-pkg", "a", "1.0.0", "v1.0.0", "a" * 40, "tar.gz", "https://github.com/zed-pkg/a", ()),
    ]
    assert [candidate.name for candidate in topological_order(sample)] == ["a", "b"]
    assert archive_format({}) == "tar.gz"
    assert archive_format({"publish": {"format": "zip"}}) == "zip"
    try:
        archive_format({"publish": {"format": "rar"}})
    except InventoryError:
        pass
    else:
        raise AssertionError("unknown archive format was accepted")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config/production-packages.json"))
    parser.add_argument("--output", type=Path, default=Path("evidence/package-inventory"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
        else:
            inventory(args.config, args.output, os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))
    except (InventoryError, OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        print(f"inventory failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
