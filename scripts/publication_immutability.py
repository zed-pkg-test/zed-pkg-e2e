#!/usr/bin/env python3
"""Fail-closed publication immutability certification for zed-pkg.

The canary publishes a fixture to a fresh file:// registry, proves that an
identical retry is byte-idempotent, then changes bytes without changing the
package identity and proves that the retry is rejected without mutating any
registry object.
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
import tarfile
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

IGNORED_SOURCE_NAMES = {
    ".git",
    ".zed-pack",
    "build",
    "node_modules",
    "target",
    "zed_modules",
}


@dataclass(frozen=True)
class PackageRef:
    org: str
    name: str
    version: str

    @property
    def spec(self) -> str:
        return f"{self.org}/{self.name}@{self.version}"


@dataclass(frozen=True)
class PublishedArtifact:
    package: PackageRef
    metadata_path: Path
    artifact_path: Path
    sha256: str


class Harness:
    def __init__(self, args: argparse.Namespace) -> None:
        self.fixture = args.fixture_dir.resolve()
        self.zed = args.zed.resolve()
        self.root = args.work_root.resolve()
        self.registry = self.root / "registry"
        self.logs = self.root / "logs"
        self.log_path = self.logs / "publication-immutability.log"

        if self.root.exists():
            raise RuntimeError(f"work root must not already exist: {self.root}")
        if not self.fixture.is_dir():
            raise RuntimeError(f"fixture directory not found: {self.fixture}")
        if not (self.fixture / ".zpkg.toml").is_file():
            raise RuntimeError(f"fixture has no .zpkg.toml: {self.fixture}")
        if not self.zed.is_file():
            raise RuntimeError(f"zed binary not found: {self.zed}")

        self.registry.mkdir(parents=True)
        self.logs.mkdir(parents=True)
        self.log_path.write_text("", encoding="utf-8")

    @property
    def registry_url(self) -> str:
        return f"file://{self.registry}"

    def environment(self, home: Path) -> dict[str, str]:
        result = os.environ.copy()
        result.update(
            {
                "CI": "true",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "ZED_PKG_HOME": str(home),
                "ZED_PKG_REGISTRY": self.registry_url,
                "ZED_PKG_TOKEN": "",
            }
        )
        return result

    def log(self, message: str) -> None:
        print(message, flush=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")

    def run(
        self,
        command: Sequence[str | Path],
        *,
        cwd: Path,
        home: Path,
        should_fail: bool = False,
        extra_env: Mapping[str, str] | None = None,
    ) -> str:
        argv = [str(value) for value in command]
        shown = " ".join(shell_quote(value) for value in argv)
        self.log(f"\n$ (cd {cwd} && {shown})")
        environment = self.environment(home)
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
        output = completed.stdout or ""
        if output:
            print(output, end="" if output.endswith("\n") else "\n", flush=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(output)
                if not output.endswith("\n"):
                    handle.write("\n")

        if should_fail:
            if completed.returncode == 0:
                raise AssertionError(f"command unexpectedly succeeded: {shown}")
        elif completed.returncode != 0:
            raise RuntimeError(
                f"command failed with exit code {completed.returncode}: {shown}"
            )
        return output

    def zed_cmd(
        self,
        *args: str | Path,
        cwd: Path,
        home: Path,
        should_fail: bool = False,
    ) -> str:
        return self.run(
            [
                self.zed,
                "--registry",
                self.registry_url,
                "--home",
                home,
                *args,
            ],
            cwd=cwd,
            home=home,
            should_fail=should_fail,
        )

    def assert_fixture_clean(self, phase: str) -> None:
        output = self.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=self.fixture,
            home=self.root / "git-home",
        )
        if output.strip():
            raise AssertionError(f"fixture became dirty during {phase}:\n{output}")

    def copy_fixture(self, destination: Path) -> Path:
        def ignore(_directory: str, names: list[str]) -> set[str]:
            return {name for name in names if name in IGNORED_SOURCE_NAMES}

        shutil.copytree(
            self.fixture,
            destination,
            symlinks=True,
            ignore=ignore,
        )
        return destination

    def publish(self, source: Path, home_name: str, *, should_fail: bool = False) -> str:
        return self.zed_cmd(
            "publish",
            "--skip-vcs-checks",
            cwd=source,
            home=self.root / home_name,
            should_fail=should_fail,
        )

    def run_contract(self) -> None:
        self.assert_fixture_clean("preflight")
        manifest = read_manifest(self.fixture)
        packages = expected_packages(manifest)
        source = self.copy_fixture(self.root / "source")

        self.log("== Initial immutable publication ==")
        self.publish(source, "home-initial")
        published = verify_registry(self.registry, packages)
        initial_registry = tree_digest(self.registry)
        if not initial_registry:
            raise AssertionError("initial publication produced no registry objects")

        self.log("== Identical retry must be byte-idempotent ==")
        self.publish(source, "home-idempotent")
        idempotent_registry = tree_digest(self.registry)
        if idempotent_registry != initial_registry:
            raise AssertionError(
                "identical same-version retry changed registry bytes or metadata"
            )
        verify_registry(self.registry, packages)

        self.log("== Same identity with changed bytes must fail closed ==")
        tampered = self.copy_fixture(self.root / "tampered")
        mutation = choose_packaged_source_file(tampered, published)
        mutate_source_file(mutation)
        self.log(
            f"mutated packaged source path: {mutation.relative_to(tampered).as_posix()}"
        )

        tampered_pack = self.root / "tampered-pack"
        self.zed_cmd(
            "pack",
            "--out",
            tampered_pack,
            cwd=tampered,
            home=self.root / "home-tampered-pack",
        )
        packed_files = [
            path
            for path in tampered_pack.rglob("*")
            if path.is_file()
            and (path.name.endswith(".tar.gz") or path.name.endswith(".zip"))
        ]
        if not packed_files:
            packed_files = [
                path for path in tampered_pack.rglob("*") if path.is_file()
            ]
        tampered_digests = {
            hashlib.sha256(path.read_bytes()).hexdigest() for path in packed_files
        }
        original_digests = {artifact.sha256 for artifact in published}
        if not tampered_digests:
            raise AssertionError("tampered source produced no packed artifact")
        if tampered_digests.issubset(original_digests):
            raise AssertionError(
                "source mutation did not change the deterministic packed artifact"
            )

        before_conflict = tree_digest(self.registry)
        output = self.publish(tampered, "home-conflict", should_fail=True)
        after_conflict = tree_digest(self.registry)
        if after_conflict != before_conflict:
            raise AssertionError(
                "rejected same-version mutation changed registry bytes; "
                "publication was not atomic"
            )

        lowered = output.lower()
        explanatory_terms = (
            "already",
            "conflict",
            "different",
            "exists",
            "immutable",
            "mismatch",
            "published",
        )
        if output.strip() and not any(term in lowered for term in explanatory_terms):
            raise AssertionError(
                "conflicting publication failed without an immutability-oriented "
                "diagnostic"
            )

        verify_registry(self.registry, packages)
        self.assert_fixture_clean("publication immutability certification")
        self.write_summary(packages, mutation, published)
        self.log("\nPASS: immutable publication contract")

    def write_summary(
        self,
        packages: list[PackageRef],
        mutation: Path,
        published: list[PublishedArtifact],
    ) -> None:
        payload = {
            "schema": "zed.publication-immutability-canary/v1",
            "registry": self.registry_url,
            "packages": [package.spec for package in packages],
            "artifacts": [
                {
                    "package": artifact.package.spec,
                    "sha256": artifact.sha256,
                    "path": artifact.artifact_path.relative_to(self.registry).as_posix(),
                }
                for artifact in published
            ],
            "mutated_source": mutation.relative_to(self.root / "tampered").as_posix(),
            "identical_retry": "idempotent",
            "changed_bytes_retry": "rejected-without-registry-mutation",
        }
        summary_path = self.root / "summary.json"
        summary_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        github_summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if github_summary:
            with Path(github_summary).open("a", encoding="utf-8") as handle:
                handle.write(
                    "### Zed publication immutability\n\n"
                    f"- package outputs: `{len(packages)}`\n"
                    "- identical same-version retry: **idempotent**\n"
                    "- changed-byte same-version retry: **rejected**\n"
                    "- registry bytes after rejection: **unchanged**\n\n"
                )


def read_manifest(directory: Path) -> dict:
    with (directory / ".zpkg.toml").open("rb") as handle:
        return tomllib.load(handle)


def expected_packages(manifest: dict) -> list[PackageRef]:
    package = manifest.get("package")
    if not isinstance(package, dict):
        raise AssertionError("manifest has no [package] table")
    org = str(package.get("org") or "")
    name = str(package.get("name") or "")
    version = str(package.get("version") or "")
    if not org or not name or not version:
        raise AssertionError("manifest package identity is incomplete")

    targets = manifest.get("targets") or {}
    if not targets:
        return [PackageRef(org, name, version)]
    if not isinstance(targets, dict):
        raise AssertionError("manifest [targets] must be a table")

    result: list[PackageRef] = []
    for key, value in sorted(targets.items()):
        target = value or {}
        if not isinstance(target, dict):
            raise AssertionError(f"target {key!r} must be a table")
        target_name = str(target.get("name") or f"{name}-{key}")
        result.append(PackageRef(org, target_name, version))
    return result


def verify_registry(
    registry: Path, packages: list[PackageRef]
) -> list[PublishedArtifact]:
    result: list[PublishedArtifact] = []
    for package in packages:
        metadata_path = (
            registry
            / "packages"
            / package.org
            / package.name
            / "versions"
            / f"{package.version}.json"
        )
        if not metadata_path.is_file():
            raise AssertionError(
                f"missing registry metadata for {package.spec}: {metadata_path}"
            )
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("org") != package.org:
            raise AssertionError(f"wrong org in {metadata_path}")
        if metadata.get("name") != package.name:
            raise AssertionError(f"wrong name in {metadata_path}")
        if str(metadata.get("version")) != package.version:
            raise AssertionError(f"wrong version in {metadata_path}")

        digest = str(metadata.get("sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise AssertionError(
                f"invalid artifact sha256 for {package.spec}: {digest!r}"
            )
        artifact_path = registry / "artifacts" / f"{digest}.tar.gz"
        if not artifact_path.is_file():
            raise AssertionError(
                f"artifact referenced by {package.spec} is missing: {artifact_path}"
            )
        actual = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        if actual != digest:
            raise AssertionError(
                f"artifact digest mismatch for {package.spec}: {actual} != {digest}"
            )
        result.append(
            PublishedArtifact(package, metadata_path, artifact_path, digest)
        )
    return result


def choose_packaged_source_file(
    source: Path, artifacts: list[PublishedArtifact]
) -> Path:
    archived_names: set[str] = set()
    for artifact in artifacts:
        with tarfile.open(artifact.artifact_path, mode="r:gz") as archive:
            for member in archive.getmembers():
                if member.isfile():
                    name = PurePosixPath(member.name).as_posix().lstrip("./")
                    archived_names.add(name)

    candidates: list[Path] = []
    for path in source.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(source).as_posix()
        if any(part in IGNORED_SOURCE_NAMES for part in path.relative_to(source).parts):
            continue
        if any(
            archived == relative or archived.endswith(f"/{relative}")
            for archived in archived_names
        ):
            candidates.append(path)

    if not candidates:
        raise AssertionError(
            "could not map any archived artifact member back to a source file"
        )

    def priority(path: Path) -> tuple[int, str]:
        relative = path.relative_to(source).as_posix()
        name = path.name.lower()
        if name.startswith("readme"):
            rank = 0
        elif path.suffix.lower() in {".md", ".txt"}:
            rank = 1
        elif path.suffix.lower() in {
            ".c",
            ".cc",
            ".cpp",
            ".dart",
            ".ex",
            ".exs",
            ".gleam",
            ".go",
            ".h",
            ".hpp",
            ".java",
            ".js",
            ".kt",
            ".mjs",
            ".py",
            ".rb",
            ".rs",
            ".ts",
            ".zig",
        }:
            rank = 2
        elif name == ".zpkg.toml":
            rank = 4
        else:
            rank = 3
        return rank, relative

    return min(candidates, key=priority)


def mutate_source_file(path: Path) -> None:
    suffix = path.suffix.lower()
    if path.name == ".zpkg.toml" or suffix == ".toml":
        marker = b"\n# zed publication immutability canary\n"
    elif suffix in {".py", ".rb"}:
        marker = b"\n# zed publication immutability canary\n"
    elif suffix in {
        ".c",
        ".cc",
        ".cpp",
        ".dart",
        ".ex",
        ".exs",
        ".gleam",
        ".go",
        ".h",
        ".hpp",
        ".java",
        ".js",
        ".kt",
        ".mjs",
        ".rs",
        ".ts",
        ".zig",
    }:
        marker = b"\n// zed publication immutability canary\n"
    else:
        marker = b"\nzed publication immutability canary\n"
    path.write_bytes(path.read_bytes() + marker)


def tree_digest(root: Path) -> tuple[tuple[str, int, str], ...]:
    if not root.exists():
        return ()
    rows: list[tuple[str, int, str]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            rows.append((relative, -1, f"symlink:{os.readlink(path)}"))
        elif path.is_file():
            data = path.read_bytes()
            rows.append((relative, len(data), hashlib.sha256(data).hexdigest()))
    return tuple(rows)


def shell_quote(value: str) -> str:
    if not value:
        return "''"
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", value):
        return value
    return "'" + value.replace("'", "'\\''") + "'"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-dir", type=Path, required=True)
    parser.add_argument("--zed", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    harness = Harness(parse_args())
    try:
        harness.run_contract()
        return 0
    except Exception as error:  # noqa: BLE001 - CI boundary preserves evidence.
        harness.log(f"\nFAIL: publication immutability: {error}")
        raise


if __name__ == "__main__":
    sys.exit(main())
