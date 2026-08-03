#!/usr/bin/env python3
"""Hermetic zed package lifecycle checks for the zed-pkg-test organization.

Every invocation owns a fresh file registry, zed home, r2g root, consumers,
and dependency clones below --work-root. It intentionally uses no credentials
and writes nothing to the checked-out fixture repository.
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
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

ORG = "zed-pkg-test"
NON_PACKAGE_REPOS = {"shared-schema", "zed-pkg-e2e"}

# Package -> (fixture repository, path containing .zpkg.toml). This is the
# dependency graph boundary. Target packages all map to the one source repo
# that fans them out; workspace packages map to their member directories.
PACKAGE_SOURCES: dict[str, tuple[str, str]] = {
    "zed-pkg-test/node-lib": ("node-lib", "."),
    "zed-pkg-test/rust-lib": ("rust-lib", "."),
    "zed-pkg-test/go-lib": ("go-lib", "."),
    "zed-pkg-test/python-lib": ("python-lib", "."),
    "zed-pkg-test/dart-lib": ("dart-lib", "."),
    "zed-pkg-test/gleam-lib": ("gleam-lib", "."),
    "zedtest/polyglot-lib-nodejs": ("polyglot-lib", "."),
    "zedtest/polyglot-lib-python": ("polyglot-lib", "."),
    "zedtest/polyglot-lib-golang": ("polyglot-lib", "."),
    "zedtest/polyglot-lib-rust": ("polyglot-lib", "."),
    "zedtest/ws-core": ("workspace-monorepo", "packages/core"),
    "zedtest/ws-utils": ("workspace-monorepo", "packages/utils"),
    "zedtest/ws-cli": ("workspace-monorepo", "apps/cli"),
}


@dataclass(frozen=True)
class PackageRef:
    full_name: str
    version: str

    @property
    def org(self) -> str:
        return self.full_name.split("/", 1)[0]

    @property
    def name(self) -> str:
        return self.full_name.split("/", 1)[1]

    @property
    def spec(self) -> str:
        return f"{self.full_name}@{self.version}"


class Harness:
    def __init__(self, args: argparse.Namespace) -> None:
        self.repo = args.repo
        self.fixture = args.fixture_dir.resolve()
        self.zed = args.zed.resolve()
        self.root = args.work_root.resolve()
        self.registry = self.root / "registry"
        self.zed_home = self.root / "zed-home"
        self.dependency_repos = self.root / "dependency-repos"
        self.diagnostics = self.root / "diagnostics"
        self.log_path = self.diagnostics / "lifecycle.log"
        self.published_sources: set[tuple[str, str]] = set()
        self.clones: dict[str, Path] = {}
        self.root.mkdir(parents=True, exist_ok=True)
        self.registry.mkdir(parents=True, exist_ok=True)
        self.zed_home.mkdir(parents=True, exist_ok=True)
        self.dependency_repos.mkdir(parents=True, exist_ok=True)
        self.diagnostics.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("", encoding="utf-8")
        if not self.zed.is_file():
            raise RuntimeError(f"zed binary not found: {self.zed}")

    @property
    def registry_url(self) -> str:
        return f"file://{self.registry}"

    def env(self, home: Path | None = None) -> dict[str, str]:
        result = os.environ.copy()
        result.update(
            {
                "CI": "true",
                "GIT_TERMINAL_PROMPT": "0",
                "ZED_PKG_REGISTRY": self.registry_url,
                "ZED_PKG_HOME": str(home or self.zed_home),
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
        cwd: Path | None = None,
        home: Path | None = None,
        should_fail: bool = False,
        extra_env: Mapping[str, str] | None = None,
    ) -> str:
        argv = [str(value) for value in command]
        shown = " ".join(shell_quote(value) for value in argv)
        self.log(f"\n$ (cd {cwd or Path.cwd()} && {shown})")
        environment = self.env(home)
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
        if completed.stdout:
            print(completed.stdout, end="", flush=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(completed.stdout)
                if not completed.stdout.endswith("\n"):
                    handle.write("\n")
        if should_fail:
            if completed.returncode == 0:
                raise AssertionError(f"command unexpectedly succeeded: {shown}")
        elif completed.returncode != 0:
            raise RuntimeError(
                f"command failed with exit code {completed.returncode}: {shown}"
            )
        return completed.stdout

    def zed_cmd(
        self,
        *args: str | Path,
        cwd: Path,
        home: Path | None = None,
        should_fail: bool = False,
    ) -> str:
        return self.run(
            [
                self.zed,
                "--registry",
                self.registry_url,
                "--home",
                home or self.zed_home,
                *args,
            ],
            cwd=cwd,
            home=home,
            should_fail=should_fail,
        )

    def assert_git_clean(self, root: Path, label: str) -> None:
        output = self.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=root
        )
        if output.strip():
            raise AssertionError(f"{label} was mutated by lifecycle tests:\n{output}")

    def source_root(self, repo: str) -> Path:
        if repo == self.repo:
            return self.fixture
        if repo in self.clones:
            return self.clones[repo]
        destination = self.dependency_repos / repo
        self.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--recurse-submodules",
                "--shallow-submodules",
                f"https://github.com/{ORG}/{repo}.git",
                destination,
            ]
        )
        self.clones[repo] = destination
        self.assert_git_clean(destination, f"dependency clone {repo}")
        return destination

    def source_for_package(self, package: str) -> tuple[str, str, Path]:
        try:
            repo, relative = PACKAGE_SOURCES[package]
        except KeyError as error:
            raise AssertionError(
                f"dependency {package!r} has no PACKAGE_SOURCES entry"
            ) from error
        root = self.source_root(repo)
        source = (root / relative).resolve()
        if not (source / ".zpkg.toml").is_file():
            raise AssertionError(f"mapped source for {package} has no manifest: {source}")
        return repo, relative, source

    def publish_prerequisite(self, package: str) -> None:
        repo, relative, source = self.source_for_package(package)
        key = (repo, relative)
        if key in self.published_sources:
            return
        manifest = read_manifest(source)
        for dependency in manifest_dependencies(manifest):
            self.publish_prerequisite(dependency)
        self.log(f"\n== Seed prerequisite {package} from {repo}/{relative} ==")
        self.zed_cmd(
            "publish",
            "--skip-vcs-checks",
            cwd=source,
            home=self.root / "dependency-publish-home",
        )
        self.published_sources.add(key)
        for output in expected_packages(manifest):
            assert_registry_version(self.registry, output)
        self.assert_git_clean(self.source_root(repo), f"seed source {repo}")

    def seed_dependencies(self, manifest: dict) -> None:
        for dependency in manifest_dependencies(manifest):
            self.publish_prerequisite(dependency)

    def run_non_package_contract(self) -> None:
        self.log(f"== {self.repo}: fail-closed non-package contract ==")
        before = tree_digest(self.registry)
        failure_commands: list[list[str | Path]] = [
            ["release", "plan", "--json"],
            ["pack", "--out", self.root / "negative-pack"],
            ["r2g", "--r2g-root", self.root / "negative-r2g", "--clean"],
            ["publish", "--dry-run", "--skip-vcs-checks"],
            ["publish", "--skip-vcs-checks"],
        ]
        for command in failure_commands:
            self.zed_cmd(*command, cwd=self.fixture, should_fail=True)
        if tree_digest(self.registry) != before:
            raise AssertionError("non-package commands wrote to the registry")
        self.assert_git_clean(self.fixture, self.repo)

    def discover_units(self) -> list[Path]:
        manifest_path = self.fixture / ".zpkg.toml"
        if not manifest_path.is_file():
            return []
        units = [self.fixture]
        manifest = read_manifest(self.fixture)
        workspace = manifest.get("workspace") or {}
        for pattern in workspace.get("members") or []:
            for candidate in sorted(self.fixture.glob(pattern)):
                if (candidate / ".zpkg.toml").is_file():
                    units.append(candidate.resolve())
        # Preserve declaration/topological fixture order but remove duplicates.
        unique: list[Path] = []
        seen: set[Path] = set()
        for unit in units:
            resolved = unit.resolve()
            if resolved not in seen:
                unique.append(resolved)
                seen.add(resolved)
        return unique

    def run_package_contract(self) -> None:
        units = self.discover_units()
        if not units:
            raise AssertionError(f"package repository {self.repo} has no .zpkg.toml")
        self.assert_git_clean(self.fixture, self.repo)
        for index, unit in enumerate(units):
            relative = unit.relative_to(self.fixture)
            label = "." if str(relative) == "." else relative.as_posix()
            self.run_unit(unit, label, index)
        self.assert_git_clean(self.fixture, self.repo)

    def run_unit(self, unit: Path, label: str, index: int) -> None:
        manifest = read_manifest(unit)
        package = manifest["package"]
        full_name = f"{package['org']}/{package['name']}"
        self.log(f"\n{'=' * 72}\n== {self.repo}:{label} ({full_name}) ==\n{'=' * 72}")
        self.seed_dependencies(manifest)

        # Release planning must be credential-free and parseable JSON.
        plan = self.zed_cmd("release", "plan", "--json", cwd=unit)
        parse_json_output(plan, f"release plan for {full_name}")

        # The source tree must yield byte-identical archives in independent dirs.
        pack_a = self.root / "pack" / f"{index}-a"
        pack_b = self.root / "pack" / f"{index}-b"
        self.zed_cmd("pack", "--out", pack_a, cwd=unit)
        self.zed_cmd("pack", "--out", pack_b, cwd=unit)
        assert_equal_pack_outputs(pack_a, pack_b, full_name)

        # r2g gets a read-only dependency input registry. PR #23 snapshots that
        # registry into the private workspace before publishing this artifact.
        dependency_registry_before = tree_digest(self.registry)
        self.zed_cmd(
            "r2g",
            "--r2g-root",
            self.root / "r2g" / str(index),
            "--clean",
            cwd=unit,
            home=self.root / "r2g-home" / str(index),
        )
        if tree_digest(self.registry) != dependency_registry_before:
            raise AssertionError("r2g mutated its configured dependency registry")

        # Dry-run is a no-write prepublish gate.
        before_dry_run = tree_digest(self.registry)
        self.zed_cmd("publish", "--dry-run", "--skip-vcs-checks", cwd=unit)
        if tree_digest(self.registry) != before_dry_run:
            raise AssertionError("publish --dry-run mutated the registry")

        # Real publish followed by an exact repeat proves file-registry writes
        # are idempotent and do not accumulate run-specific timestamps/state.
        self.zed_cmd("publish", "--skip-vcs-checks", cwd=unit)
        outputs = expected_packages(manifest)
        for output in outputs:
            assert_registry_version(self.registry, output)
        after_first_publish = tree_digest(self.registry)
        self.zed_cmd("publish", "--skip-vcs-checks", cwd=unit)
        after_second_publish = tree_digest(self.registry)
        if after_second_publish != after_first_publish:
            raise AssertionError(
                f"second publish changed registry bytes for {full_name}"
            )

        for output_index, output in enumerate(outputs):
            self.exercise_consumer(output, index, output_index)
        self.assert_git_clean(self.fixture, self.repo)

    def exercise_consumer(
        self, package: PackageRef, unit_index: int, output_index: int
    ) -> None:
        self.log(f"\n-- Consumer lifecycle: {package.spec} --")
        search = self.zed_cmd("find", package.name, cwd=self.fixture)
        if package.full_name not in search:
            raise AssertionError(f"zed find did not return {package.full_name}")

        consumer_root = self.root / "consumers" / str(unit_index) / str(output_index)
        cold = consumer_root / "cold"
        write_consumer_manifest(cold, package)
        cold_home = consumer_root / "home-cold"
        self.zed_cmd(
            "install",
            "--install-mode",
            "copy",
            "--adapter",
            "none",
            "--allow-ecosystem-mismatch",
            cwd=cold,
            home=cold_home,
        )
        assert_copy_install(cold, package)
        lock_bytes = (cold / ".zpkg.lock").read_bytes()

        frozen = consumer_root / "frozen"
        frozen.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cold / ".zpkg.toml", frozen / ".zpkg.toml")
        shutil.copy2(cold / ".zpkg.lock", frozen / ".zpkg.lock")
        self.zed_cmd(
            "install",
            "--frozen",
            "--install-mode",
            "copy",
            "--adapter",
            "none",
            "--allow-ecosystem-mismatch",
            cwd=frozen,
            home=consumer_root / "home-frozen",
        )
        assert_copy_install(frozen, package)
        if (frozen / ".zpkg.lock").read_bytes() != lock_bytes:
            raise AssertionError(f"frozen install rewrote lockfile for {package.spec}")

        # A yank blocks new resolution but must not break a pre-existing lock.
        self.zed_cmd("yank", package.spec, cwd=self.fixture)
        blocked = consumer_root / "blocked"
        write_consumer_manifest(blocked, package)
        self.zed_cmd(
            "install",
            "--install-mode",
            "copy",
            "--adapter",
            "none",
            "--allow-ecosystem-mismatch",
            cwd=blocked,
            home=consumer_root / "home-blocked",
            should_fail=True,
        )

        frozen_yanked = consumer_root / "frozen-yanked"
        frozen_yanked.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cold / ".zpkg.toml", frozen_yanked / ".zpkg.toml")
        shutil.copy2(cold / ".zpkg.lock", frozen_yanked / ".zpkg.lock")
        self.zed_cmd(
            "install",
            "--frozen",
            "--install-mode",
            "copy",
            "--adapter",
            "none",
            "--allow-ecosystem-mismatch",
            cwd=frozen_yanked,
            home=consumer_root / "home-frozen-yanked",
        )
        assert_copy_install(frozen_yanked, package)

        self.zed_cmd("yank", package.spec, "--undo", cwd=self.fixture)
        restored = consumer_root / "restored"
        write_consumer_manifest(restored, package)
        self.zed_cmd(
            "install",
            "--install-mode",
            "copy",
            "--adapter",
            "none",
            "--allow-ecosystem-mismatch",
            cwd=restored,
            home=consumer_root / "home-restored",
        )
        assert_copy_install(restored, package)

    def finish(self) -> None:
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with Path(summary).open("a", encoding="utf-8") as handle:
                handle.write(
                    f"### `{self.repo}` lifecycle\n\n"
                    f"- isolated registry: `{self.registry}`\n"
                    f"- dependency clones: `{self.dependency_repos}`\n"
                    f"- diagnostics: `{self.diagnostics}`\n"
                    "- result: PASS\n\n"
                )


def read_manifest(directory: Path) -> dict:
    with (directory / ".zpkg.toml").open("rb") as handle:
        return tomllib.load(handle)


def manifest_dependencies(manifest: dict) -> list[str]:
    values: list[str] = []
    for key in ("dependencies", "build_dependencies", "build-dependencies"):
        section = manifest.get(key) or {}
        values.extend(str(name) for name in section)
    return sorted(set(values))


def expected_packages(manifest: dict) -> list[PackageRef]:
    package = manifest["package"]
    org = str(package["org"])
    base_name = str(package["name"])
    version = str(package["version"])
    targets = manifest.get("targets") or {}
    if not targets:
        return [PackageRef(f"{org}/{base_name}", version)]
    result: list[PackageRef] = []
    for key, target in sorted(targets.items()):
        name = str((target or {}).get("name") or f"{base_name}-{key}")
        result.append(PackageRef(f"{org}/{name}", version))
    return result


def assert_registry_version(registry: Path, package: PackageRef) -> None:
    version = (
        registry
        / "packages"
        / package.org
        / package.name
        / "versions"
        / f"{package.version}.json"
    )
    if not version.is_file():
        raise AssertionError(f"missing registry metadata for {package.spec}: {version}")
    payload = json.loads(version.read_text(encoding="utf-8"))
    if payload.get("org") != package.org:
        raise AssertionError(f"wrong org in {version}")
    if payload.get("name") != package.name:
        raise AssertionError(f"wrong name in {version}")
    if str(payload.get("version")) != package.version:
        raise AssertionError(f"wrong version in {version}")
    sha = str(payload.get("sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise AssertionError(f"invalid artifact sha256 in {version}: {sha!r}")
    artifact = registry / "artifacts" / f"{sha}.tar.gz"
    if not artifact.is_file():
        raise AssertionError(f"artifact referenced by {version} is missing: {artifact}")
    actual = hashlib.sha256(artifact.read_bytes()).hexdigest()
    if actual != sha:
        raise AssertionError(f"artifact digest mismatch for {package.spec}")


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


def assert_equal_pack_outputs(first: Path, second: Path, package: str) -> None:
    left = tree_digest(first)
    right = tree_digest(second)
    if not left:
        raise AssertionError(f"zed pack produced no files for {package}")
    if left != right:
        raise AssertionError(f"zed pack was not deterministic for {package}")


def parse_json_output(output: str, label: str) -> object:
    candidates = [line.strip() for line in output.splitlines() if line.strip()]
    for start in range(len(candidates)):
        text = "\n".join(candidates[start:])
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            continue
    raise AssertionError(f"{label} did not emit parseable JSON")


def write_consumer_manifest(directory: Path, package: PackageRef) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9-]+", "-", package.name.lower()).strip("-") or "pkg"
    content = f'''[package]\norg = "zed-lifecycle"\nname = "consumer-{slug}"\nversion = "0.0.0"\ndescription = "Stateless lifecycle consumer for {package.full_name}"\n\n[package.repository]\nvcs = "git"\nurl = "https://localhost/zed-lifecycle/consumer-{slug}"\n\n[install]\ndir = "zed_modules"\n\n[dependencies]\n"{package.full_name}" = "={package.version}"\n'''
    (directory / ".zpkg.toml").write_text(content, encoding="utf-8")


def assert_copy_install(consumer: Path, package: PackageRef) -> None:
    lock = consumer / ".zpkg.lock"
    if not lock.is_file():
        raise AssertionError(f"install did not create {lock}")
    installed = consumer / "zed_modules" / package.org / package.name
    if not (installed / ".zpkg.toml").is_file():
        raise AssertionError(f"package not materialized at {installed}")
    for path in consumer.rglob("*"):
        if path.is_symlink():
            raise AssertionError(f"copy install leaked symlink: {path}")


def shell_quote(value: str) -> str:
    if not value:
        return "''"
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", value):
        return value
    return "'" + value.replace("'", "'\\''") + "'"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--fixture-dir", type=Path, required=True)
    parser.add_argument("--zed", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    harness = Harness(args)
    try:
        if args.repo in NON_PACKAGE_REPOS:
            harness.run_non_package_contract()
        else:
            harness.run_package_contract()
        harness.finish()
        harness.log(f"\nPASS: {args.repo}")
        return 0
    except Exception as error:  # noqa: BLE001 - CI boundary needs diagnostics.
        harness.log(f"\nFAIL: {args.repo}: {error}")
        raise


if __name__ == "__main__":
    sys.exit(main())
