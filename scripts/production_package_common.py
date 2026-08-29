#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SHA_RE_LENGTH = 40
MIN_PACKAGES = 15
MAX_PACKAGES = 25


class CertificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Package:
    github_owner: str
    repo: str
    package: str
    org: str
    name: str
    version: str
    sha: str
    tag: str | None
    dependencies: tuple[str, ...]
    allow_repo_name_mismatch: bool

    @property
    def source_url(self) -> str:
        return f"https://github.com/{self.github_owner}/{self.repo}.git"

    @property
    def spec(self) -> str:
        return f"{self.package}@{self.version}"


@dataclass(frozen=True)
class RegistryPackage:
    logical_package: str
    org: str
    name: str
    version: str
    target: str | None

    @property
    def package(self) -> str:
        return f"{self.org}/{self.name}"

    @property
    def spec(self) -> str:
        return f"{self.package}@{self.version}"


@dataclass
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float


def command_display(command: Iterable[str]) -> str:
    return " ".join(subprocess.list2cmdline([part]) for part in command)


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    timeout: int = 1800,
) -> CommandResult:
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    result = CommandResult(
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        duration_seconds=round(time.monotonic() - started, 3),
    )
    print(f"$ {command_display(command)}", flush=True)
    if result.stdout:
        print(
            result.stdout,
            end="" if result.stdout.endswith("\n") else "\n",
            flush=True,
        )
    if result.stderr:
        print(
            result.stderr,
            end="" if result.stderr.endswith("\n") else "\n",
            file=sys.stderr,
            flush=True,
        )
    if check and result.returncode != 0:
        raise CertificationError(
            f"command failed ({result.returncode}): {command_display(command)}"
        )
    return result


def load_ledger(path: Path) -> tuple[dict[str, Any], list[Package]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema") != 1:
        raise CertificationError("unsupported release-ledger schema")
    rows = raw.get("packages")
    if not isinstance(rows, list):
        raise CertificationError("release ledger packages must be a list")

    packages: list[Package] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise CertificationError(f"package row {index} is not an object")

        allow_mismatch = row.get("allow_repo_name_mismatch", False)
        if not isinstance(allow_mismatch, bool):
            raise CertificationError(
                f"package row {index}: allow_repo_name_mismatch must be a boolean"
            )

        package = Package(
            github_owner=str(row["github_owner"]),
            repo=str(row["repo"]),
            package=str(row["package"]),
            org=str(row["org"]),
            name=str(row["name"]),
            version=str(row["version"]),
            sha=str(row["sha"]),
            tag=str(row["tag"]) if row.get("tag") else None,
            dependencies=tuple(str(item) for item in row.get("dependencies", [])),
            allow_repo_name_mismatch=allow_mismatch,
        )

        if package.package != f"{package.org}/{package.name}":
            raise CertificationError(f"invalid package coordinate: {package.package}")

        repository_name_mismatch = package.repo != package.name
        if repository_name_mismatch and not package.allow_repo_name_mismatch:
            raise CertificationError(
                f"{package.package}: repository/name mismatch "
                f"{package.repo!r} != {package.name!r}; declare the exception explicitly"
            )
        if package.allow_repo_name_mismatch and not repository_name_mismatch:
            raise CertificationError(
                f"{package.package}: unnecessary repository/name mismatch exception"
            )

        if len(package.sha) != SHA_RE_LENGTH or any(
            c not in "0123456789abcdef" for c in package.sha
        ):
            raise CertificationError(
                f"{package.package}: invalid source SHA {package.sha!r}"
            )
        packages.append(package)

    expected_count = int(raw.get("count", -1))
    if expected_count != len(packages):
        raise CertificationError(
            f"ledger count {expected_count} does not match {len(packages)} package rows"
        )
    if not MIN_PACKAGES <= len(packages) <= MAX_PACKAGES:
        raise CertificationError(
            f"package count {len(packages)} is outside [{MIN_PACKAGES}, {MAX_PACKAGES}]"
        )

    coordinates = [package.package for package in packages]
    if len(coordinates) != len(set(coordinates)):
        raise CertificationError("release ledger contains duplicate package coordinates")
    if "zed-pkg/zed-lib-core" not in coordinates:
        raise CertificationError("zed-pkg/zed-lib-core is mandatory")

    selected = set(coordinates)
    seen: set[str] = set()
    for package in packages:
        missing = sorted(set(package.dependencies) - selected)
        if missing:
            raise CertificationError(
                f"{package.package}: dependency closure is incomplete: {missing}"
            )
        late = sorted(set(package.dependencies) - seen)
        if late:
            raise CertificationError(
                f"{package.package}: ledger is not topological; "
                f"dependencies appear later: {late}"
            )
        seen.add(package.package)

    return raw, packages
