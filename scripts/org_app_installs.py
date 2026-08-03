#!/usr/bin/env python3
"""Install real zed-pkg-test app fixtures and verify adapter/workspace behavior."""

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

GITHUB_ORG = "zed-pkg-test"
APP_REPOS = (
    "node-app",
    "rust-app",
    "go-app",
    "python-app",
    "dart-app",
    "gleam-app",
    "polyglot-node-app",
    "polyglot-go-app",
)
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
    "zedtest/shared-schema": ("shared-schema", "."),
}
EXPECTED_ADAPTER = {
    "node-app": "node",
    "rust-app": "rust",
    "go-app": "go",
    "python-app": "python",
    "dart-app": "dart",
    "gleam-app": "none",
    "polyglot-node-app": "node",
    "polyglot-go-app": "go",
}
PREFETCH_RE = re.compile(
    r"recursive install prefetch: (?P<resolved>\d+) package\(s\), "
    r"up to (?P<concurrency>\d+) concurrent, (?P<downloaded>\d+) downloaded"
)
GENERATED_WIRING_FILES = (
    "paths.json",
    "node_path",
    "classpath",
    "go.work",
    "pythonpath",
    "cargo-paths.toml",
    "pub-deps.yaml",
)


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


@dataclass(frozen=True)
class Completed:
    command: tuple[str, ...]
    returncode: int
    output: str


def shell_quote(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:@%+=,-]+", value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def run(
    command: Sequence[str | Path],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int = 300,
) -> Completed:
    argv = tuple(str(value) for value in command)
    shown = " ".join(shell_quote(value) for value in argv)
    print(f"\n$ (cd {cwd or Path.cwd()} && {shown})", flush=True)
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=timeout,
    )
    if completed.stdout:
        print(completed.stdout, end="", flush=True)
    return Completed(argv, completed.returncode, completed.stdout)


def require_success(completed: Completed) -> None:
    if completed.returncode != 0:
        shown = " ".join(shell_quote(value) for value in completed.command)
        raise RuntimeError(f"command failed with exit code {completed.returncode}: {shown}")


def require_failure(completed: Completed, *fragments: str) -> None:
    if completed.returncode == 0:
        shown = " ".join(shell_quote(value) for value in completed.command)
        raise AssertionError(f"command unexpectedly succeeded: {shown}")
    for fragment in fragments:
        if fragment not in completed.output:
            raise AssertionError(
                f"failed command output did not contain {fragment!r}:\n{completed.output}"
            )


def tree_fingerprint(root: Path) -> tuple[tuple[str, int, str], ...]:
    if not root.exists():
        return ()
    rows: list[tuple[str, int, str]] = []
    for candidate in sorted(root.rglob("*")):
        if candidate.is_file() and not candidate.is_symlink():
            data = candidate.read_bytes()
            rows.append(
                (
                    candidate.relative_to(root).as_posix(),
                    len(data),
                    hashlib.sha256(data).hexdigest(),
                )
            )
    return tuple(rows)


def zed_command(zed: Path, registry: Path, home: Path, *args: str) -> list[str | Path]:
    return [
        zed,
        "--registry",
        f"file://{registry}",
        "--home",
        home,
        *args,
    ]


def read_manifest(directory: Path) -> dict:
    with (directory / ".zpkg.toml").open("rb") as handle:
        return tomllib.load(handle)


def dependencies(manifest: dict) -> dict[str, str]:
    section = manifest.get("dependencies") or {}
    if not isinstance(section, dict):
        raise AssertionError("[dependencies] must be a table")
    return {str(key): str(value) for key, value in section.items()}


def expected_packages(manifest: dict) -> list[PackageRef]:
    package = manifest["package"]
    org = str(package["org"])
    base_name = str(package["name"])
    version = str(package["version"])
    targets = manifest.get("targets") or {}
    if not targets:
        return [PackageRef(f"{org}/{base_name}", version)]
    result: list[PackageRef] = []
    for target_name, target in sorted(targets.items()):
        target_table = target or {}
        name = str(target_table.get("name") or f"{base_name}-{target_name}")
        result.append(PackageRef(f"{org}/{name}", version))
    return result


def modules_dir(manifest: dict) -> str:
    install = manifest.get("install") or {}
    return str(install.get("dir") or "zed_modules")


def clone_repo(root: Path, repo: str, clones: dict[str, Path]) -> Path:
    if repo in clones:
        return clones[repo]
    destination = root / repo
    completed = run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--recurse-submodules",
            "--shallow-submodules",
            f"https://github.com/{GITHUB_ORG}/{repo}.git",
            destination,
        ],
        timeout=300,
    )
    require_success(completed)
    clones[repo] = destination
    return destination


def copy_fixture(source: Path, destination: Path) -> None:
    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = {name for name in names if name in {".git", ".zed", ".zpkg-staging"}}
        if Path(directory) == source:
            ignored.update(name for name in names if name == ".zpkg.lock")
        return ignored

    shutil.copytree(source, destination, symlinks=True, ignore=ignore)


def registry_metadata_path(registry: Path, package: PackageRef) -> Path:
    return (
        registry
        / "packages"
        / package.org
        / package.name
        / "versions"
        / f"{package.version}.json"
    )


def assert_registry_package(registry: Path, package: PackageRef) -> None:
    metadata_path = registry_metadata_path(registry, package)
    if not metadata_path.is_file():
        raise AssertionError(f"missing registry metadata: {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    sha256 = str(metadata.get("sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise AssertionError(f"invalid SHA in {metadata_path}: {sha256!r}")
    artifact = registry / "artifacts" / f"{sha256}.tar.gz"
    if not artifact.is_file():
        raise AssertionError(f"missing registry artifact: {artifact}")


def parse_lock(project: Path) -> list[dict[str, object]]:
    with (project / ".zpkg.lock").open("rb") as handle:
        document = tomllib.load(handle)
    packages = document.get("package") or []
    if not isinstance(packages, list):
        raise AssertionError(f"invalid lock package table in {project}")
    return packages


def package_key(package: Mapping[str, object]) -> str:
    return f"{package['org']}/{package['name']}"


def materialized_path(project: Path, install_dir: str, package: Mapping[str, object]) -> Path:
    return project / install_dir / str(package["org"]) / str(package["name"])


def assert_symlink_graph(
    project: Path,
    home: Path,
    install_dir: str,
    packages: Sequence[Mapping[str, object]],
) -> dict[str, Path]:
    store = (home / "store").resolve()
    targets: dict[str, Path] = {}
    for package in packages:
        key = package_key(package)
        installed = materialized_path(project, install_dir, package)
        if not installed.is_symlink():
            raise AssertionError(f"{project.name}: expected symlink for {key}: {installed}")
        target = installed.resolve(strict=True)
        try:
            target.relative_to(store)
        except ValueError as error:
            raise AssertionError(f"{installed} resolves outside {store}: {target}") from error
        if not (target / ".zpkg.toml").is_file():
            raise AssertionError(f"installed artifact has no manifest: {target}")
        targets[key] = target
    return targets


def assert_paths_index(project: Path, keys: Sequence[str]) -> None:
    path = project / ".zed" / "paths.json"
    if not path.is_file():
        raise AssertionError(f"missing adapter-independent path index: {path}")
    text = path.read_text(encoding="utf-8")
    json.loads(text)
    for key in keys:
        if key not in text:
            raise AssertionError(f"{path} does not mention installed package {key}")


def assert_adapter(
    project: Path,
    adapter: str,
    install_dir: str,
    packages: Sequence[Mapping[str, object]],
) -> None:
    keys = [package_key(package) for package in packages]
    assert_paths_index(project, keys)
    zed_dir = project / ".zed"

    if adapter == "node":
        node_path = zed_dir / "node_path"
        if node_path.read_text(encoding="utf-8").strip() != install_dir:
            raise AssertionError(f"unexpected node_path in {project}: {node_path.read_text()!r}")
        for package in packages:
            node_link = (
                project
                / "node_modules"
                / f"@{package['org']}"
                / str(package["name"])
            )
            if not node_link.is_symlink():
                raise AssertionError(f"missing Node adapter symlink: {node_link}")
            if node_link.resolve(strict=True) != materialized_path(
                project, install_dir, package
            ).resolve(strict=True):
                raise AssertionError(f"Node adapter target mismatch: {node_link}")
    elif adapter == "go":
        wiring = zed_dir / "go.work"
        if not wiring.is_file():
            raise AssertionError(f"missing Go wiring: {wiring}")
        text = wiring.read_text(encoding="utf-8")
        for package in packages:
            if str(package["name"]) not in text:
                raise AssertionError(f"{wiring} omits {package_key(package)}")
    elif adapter == "python":
        wiring = zed_dir / "pythonpath"
        if not wiring.is_file():
            raise AssertionError(f"missing Python wiring: {wiring}")
        text = wiring.read_text(encoding="utf-8")
        for package in packages:
            if str(package["name"]) not in text:
                raise AssertionError(f"{wiring} omits {package_key(package)}")
    elif adapter == "rust":
        wiring = zed_dir / "cargo-paths.toml"
        if not wiring.is_file():
            raise AssertionError(f"missing Rust wiring: {wiring}")
        text = wiring.read_text(encoding="utf-8")
        for package in packages:
            if str(package["name"]) not in text:
                raise AssertionError(f"{wiring} omits {package_key(package)}")
    elif adapter == "dart":
        wiring = zed_dir / "pub-deps.yaml"
        if not wiring.is_file():
            raise AssertionError(f"missing Dart wiring: {wiring}")
        text = wiring.read_text(encoding="utf-8")
        for package in packages:
            if str(package["name"]) not in text:
                raise AssertionError(f"{wiring} omits {package_key(package)}")
    elif adapter != "none":
        raise AssertionError(f"unknown expected adapter: {adapter}")


def assert_not_materialized(
    project: Path,
    install_dir: str,
    packages: Sequence[Mapping[str, object]],
) -> None:
    for package in packages:
        path = materialized_path(project, install_dir, package)
        if path.exists() or path.is_symlink():
            raise AssertionError(f"uninstall left {path}")
        node_path = (
            project
            / "node_modules"
            / f"@{package['org']}"
            / str(package["name"])
        )
        if node_path.exists() or node_path.is_symlink():
            raise AssertionError(f"uninstall left Node adapter path {node_path}")


def assert_generated_wiring_absent(project: Path) -> None:
    leaked = [
        project / ".zed" / name
        for name in GENERATED_WIRING_FILES
        if (project / ".zed" / name).exists()
        or (project / ".zed" / name).is_symlink()
    ]
    if leaked:
        raise AssertionError(f"uninstall left generated adapter wiring: {leaked}")


def assert_no_staging(project: Path) -> None:
    staging = project / ".zpkg-staging"
    if staging.exists():
        raise AssertionError(f"transaction staging leaked in {project}: {list(staging.iterdir())}")


def parse_prefetch(output: str) -> tuple[int, int, int]:
    matches = list(PREFETCH_RE.finditer(output))
    if len(matches) != 1:
        raise AssertionError(f"expected one prefetch summary, got {len(matches)}:\n{output}")
    match = matches[0]
    return tuple(
        int(match.group(field))
        for field in ("resolved", "concurrency", "downloaded")
    )


def publish_prerequisite(
    package_key_value: str,
    *,
    zed: Path,
    registry: Path,
    publish_home: Path,
    clone_root: Path,
    clones: dict[str, Path],
    published_sources: set[tuple[str, str]],
    environment: Mapping[str, str],
) -> None:
    try:
        repo, relative = PACKAGE_SOURCES[package_key_value]
    except KeyError as error:
        raise AssertionError(
            f"no source mapping for dependency {package_key_value!r}"
        ) from error
    source_root = clone_repo(clone_root, repo, clones)
    source = (source_root / relative).resolve()
    source_manifest = read_manifest(source)
    for dependency in dependencies(source_manifest):
        publish_prerequisite(
            dependency,
            zed=zed,
            registry=registry,
            publish_home=publish_home,
            clone_root=clone_root,
            clones=clones,
            published_sources=published_sources,
            environment=environment,
        )

    source_key = (repo, relative)
    if source_key in published_sources:
        return
    completed = run(
        zed_command(
            zed,
            registry,
            publish_home,
            "publish",
            "--skip-vcs-checks",
        ),
        cwd=source,
        env=environment,
    )
    require_success(completed)
    published_sources.add(source_key)
    for output in expected_packages(source_manifest):
        assert_registry_package(registry, output)


def exercise_app(
    repo: str,
    *,
    zed: Path,
    registry: Path,
    shared_home: Path,
    clone_root: Path,
    app_root: Path,
    clones: dict[str, Path],
    published_sources: set[tuple[str, str]],
    environment: Mapping[str, str],
) -> tuple[int, set[str]]:
    source = clone_repo(clone_root, repo, clones)
    source_manifest = read_manifest(source)
    direct = dependencies(source_manifest)
    if not direct:
        raise AssertionError(f"app fixture {repo} has no Zed dependencies")
    for dependency in direct:
        publish_prerequisite(
            dependency,
            zed=zed,
            registry=registry,
            publish_home=app_root.parent / "publish-home",
            clone_root=clone_root,
            clones=clones,
            published_sources=published_sources,
            environment=environment,
        )

    project = app_root / repo
    copy_fixture(source, project)
    manifest_bytes = (project / ".zpkg.toml").read_bytes()
    install_dir = modules_dir(source_manifest)

    cold = run(
        zed_command(zed, registry, shared_home, "install"),
        cwd=project,
        env=environment,
    )
    require_success(cold)
    packages = parse_lock(project)
    keys = {package_key(package) for package in packages}
    if not set(direct).issubset(keys):
        raise AssertionError(
            f"{repo}: lock omits direct dependencies {sorted(set(direct) - keys)}"
        )
    summary = parse_prefetch(cold.output)
    if summary[:2] != (len(packages), 5):
        raise AssertionError(f"{repo}: unexpected cold prefetch summary {summary}")

    targets = assert_symlink_graph(project, shared_home, install_dir, packages)
    assert_adapter(project, EXPECTED_ADAPTER[repo], install_dir, packages)
    assert_no_staging(project)
    lock_bytes = (project / ".zpkg.lock").read_bytes()

    warm = run(
        zed_command(zed, registry, shared_home, "install", "--frozen"),
        cwd=project,
        env=environment,
    )
    require_success(warm)
    if parse_prefetch(warm.output) != (len(packages), 5, 0):
        raise AssertionError(f"{repo}: warm frozen install downloaded bytes")
    if (project / ".zpkg.lock").read_bytes() != lock_bytes:
        raise AssertionError(f"{repo}: warm frozen install rewrote the lockfile")
    if assert_symlink_graph(project, shared_home, install_dir, packages) != targets:
        raise AssertionError(f"{repo}: warm frozen install changed store targets")

    first = packages[0]
    materialized_path(project, install_dir, first).unlink()
    repaired = run(
        zed_command(zed, registry, shared_home, "install", "--frozen"),
        cwd=project,
        env=environment,
    )
    require_success(repaired)
    if parse_prefetch(repaired.output) != (len(packages), 5, 0):
        raise AssertionError(f"{repo}: repair downloaded bytes")
    assert_symlink_graph(project, shared_home, install_dir, packages)
    assert_adapter(project, EXPECTED_ADAPTER[repo], install_dir, packages)

    uninstalled = run(
        zed_command(zed, registry, shared_home, "uninstall"),
        cwd=project,
        env=environment,
    )
    require_success(uninstalled)
    assert_not_materialized(project, install_dir, packages)
    assert_generated_wiring_absent(project)
    if (project / ".zpkg.lock").read_bytes() != lock_bytes:
        raise AssertionError(f"{repo}: uninstall rewrote the retained lockfile")
    if (project / ".zpkg.toml").read_bytes() != manifest_bytes:
        raise AssertionError(f"{repo}: uninstall rewrote the manifest")
    assert_no_staging(project)

    restored = run(
        zed_command(zed, registry, shared_home, "install", "--frozen"),
        cwd=project,
        env=environment,
    )
    require_success(restored)
    if parse_prefetch(restored.output) != (len(packages), 5, 0):
        raise AssertionError(f"{repo}: post-uninstall restore downloaded bytes")
    assert_symlink_graph(project, shared_home, install_dir, packages)
    assert_adapter(project, EXPECTED_ADAPTER[repo], install_dir, packages)
    assert_no_staging(project)

    return summary[2], {str(package["sha256"]) for package in packages}


def exercise_workspace(
    *,
    zed: Path,
    registry: Path,
    home: Path,
    clone_root: Path,
    workspace_root: Path,
    clones: dict[str, Path],
    environment: Mapping[str, str],
) -> None:
    source = clone_repo(clone_root, "workspace-monorepo", clones)

    def expected_sources(repo_root: Path) -> dict[str, Path]:
        return {
            "ws-utils": repo_root / "packages" / "utils",
            "ws-core": repo_root / "packages" / "core",
        }

    def assert_projection(repo_root: Path, project: Path, mode: str) -> None:
        expected = expected_sources(repo_root)
        path_index = project / ".zed" / "paths.json"
        if not path_index.is_file():
            raise AssertionError(f"workspace install omitted {path_index}")
        index_text = path_index.read_text(encoding="utf-8")
        json.loads(index_text)
        for name, expected_source in expected.items():
            key = f"zedtest/{name}"
            installed = project / "zed_modules" / "zedtest" / name
            node_link = project / "node_modules" / "@zedtest" / name
            for projection in (installed, node_link):
                if mode == "symlink":
                    if not projection.is_symlink():
                        raise AssertionError(
                            f"workspace dependency was not symlinked: {projection}"
                        )
                    if projection.resolve(strict=True) != expected_source.resolve(
                        strict=True
                    ):
                        raise AssertionError(
                            f"workspace target mismatch: {projection} -> {projection.resolve()}"
                        )
                else:
                    if projection.is_symlink() or not projection.is_dir():
                        raise AssertionError(
                            f"workspace dependency was not copied: {projection}"
                        )
                    if not (projection / ".zpkg.toml").is_file():
                        raise AssertionError(
                            f"workspace copied projection has no manifest: {projection}"
                        )
            if key not in index_text or '"version": "workspace"' not in index_text:
                raise AssertionError(f"workspace path index omits {key}: {index_text}")

        node_path = project / ".zed" / "node_path"
        if node_path.read_text(encoding="utf-8").strip() != "zed_modules":
            raise AssertionError(f"unexpected workspace node_path: {node_path.read_text()!r}")

    symlink_repo = workspace_root / "symlink"
    copy_fixture(source, symlink_repo)
    symlink_project = symlink_repo / "apps" / "cli"
    symlink_install = run(
        zed_command(zed, registry, home, "install"),
        cwd=symlink_project,
        env=environment,
    )
    require_success(symlink_install)
    if PREFETCH_RE.search(symlink_install.output):
        raise AssertionError(
            "workspace-only install unexpectedly reported registry artifact prefetch"
        )
    if parse_lock(symlink_project):
        raise AssertionError("workspace packages must not be written as registry lock entries")
    symlink_lock = (symlink_project / ".zpkg.lock").read_bytes()
    assert_projection(symlink_repo, symlink_project, "symlink")

    live_sentinel = symlink_repo / "packages" / "core" / "live-sentinel.txt"
    live_sentinel.write_text("visible after install\n", encoding="utf-8")
    visible = (
        symlink_project
        / "zed_modules"
        / "zedtest"
        / "ws-core"
        / "live-sentinel.txt"
    )
    if visible.read_text(encoding="utf-8") != "visible after install\n":
        raise AssertionError("workspace symlink did not expose a post-install source edit")

    registry_before_frozen = tree_fingerprint(registry)
    cache_before_frozen = tree_fingerprint(home / "cache")
    for projection in [
        symlink_project / "zed_modules" / "zedtest" / "ws-utils",
        symlink_project / "zed_modules" / "zedtest" / "ws-core",
        symlink_project / "node_modules" / "@zedtest" / "ws-utils",
        symlink_project / "node_modules" / "@zedtest" / "ws-core",
    ]:
        projection.unlink()

    frozen_repair = run(
        zed_command(zed, registry, home, "install", "--frozen"),
        cwd=symlink_project,
        env=environment,
    )
    require_success(frozen_repair)
    if PREFETCH_RE.search(frozen_repair.output):
        raise AssertionError("frozen workspace repair accessed registry artifacts")
    if (symlink_project / ".zpkg.lock").read_bytes() != symlink_lock:
        raise AssertionError("frozen workspace repair rewrote the empty artifact lock")
    if tree_fingerprint(registry) != registry_before_frozen:
        raise AssertionError("frozen workspace repair mutated the registry")
    if tree_fingerprint(home / "cache") != cache_before_frozen:
        raise AssertionError("frozen workspace repair mutated the artifact cache")
    assert_projection(symlink_repo, symlink_project, "symlink")

    manifest_path = symlink_project / ".zpkg.toml"
    manifest_before = manifest_path.read_text(encoding="utf-8")
    incompatible = re.sub(
        r'("zedtest/ws-utils"\s*=\s*)"[^"]+"',
        r'\1"^2"',
        manifest_before,
        count=1,
    )
    if incompatible == manifest_before:
        raise AssertionError("could not rewrite workspace requirement for drift test")
    manifest_path.write_text(incompatible, encoding="utf-8")
    version_drift = run(
        zed_command(zed, registry, home, "install", "--frozen"),
        cwd=symlink_project,
        env=environment,
    )
    require_failure(version_drift, "ws-utils", "does not satisfy `^2`")
    if (symlink_project / ".zpkg.lock").read_bytes() != symlink_lock:
        raise AssertionError("workspace version-drift failure rewrote the lock")
    assert_projection(symlink_repo, symlink_project, "symlink")
    manifest_path.write_text(manifest_before, encoding="utf-8")

    symlink_uninstall = run(
        zed_command(zed, registry, home, "uninstall"),
        cwd=symlink_project,
        env=environment,
    )
    require_success(symlink_uninstall)
    for projection in [
        symlink_project / "zed_modules" / "zedtest" / "ws-utils",
        symlink_project / "zed_modules" / "zedtest" / "ws-core",
        symlink_project / "node_modules" / "@zedtest" / "ws-utils",
        symlink_project / "node_modules" / "@zedtest" / "ws-core",
    ]:
        if projection.exists() or projection.is_symlink():
            raise AssertionError(f"workspace uninstall left projection: {projection}")
    assert_generated_wiring_absent(symlink_project)
    assert_no_staging(symlink_project)
    if (symlink_project / ".zpkg.lock").read_bytes() != symlink_lock:
        raise AssertionError("workspace uninstall rewrote the retained lock")

    symlink_restore = run(
        zed_command(zed, registry, home, "install", "--frozen"),
        cwd=symlink_project,
        env=environment,
    )
    require_success(symlink_restore)
    if PREFETCH_RE.search(symlink_restore.output):
        raise AssertionError("workspace post-uninstall restore accessed registry artifacts")
    if (symlink_project / ".zpkg.lock").read_bytes() != symlink_lock:
        raise AssertionError("workspace post-uninstall restore rewrote the lock")
    assert_projection(symlink_repo, symlink_project, "symlink")
    assert_no_staging(symlink_project)

    copy_repo = workspace_root / "copy"
    copy_fixture(source, copy_repo)
    copy_source_sentinel = copy_repo / "packages" / "core" / "copy-sentinel.txt"
    copy_source_sentinel.write_text("before install\n", encoding="utf-8")
    copy_project = copy_repo / "apps" / "cli"
    copy_install = run(
        zed_command(
            zed,
            registry,
            home,
            "install",
            "--install-mode",
            "copy",
        ),
        cwd=copy_project,
        env=environment,
    )
    require_success(copy_install)
    if PREFETCH_RE.search(copy_install.output):
        raise AssertionError("workspace copy install accessed registry artifacts")
    if parse_lock(copy_project):
        raise AssertionError("workspace copy install wrote registry lock entries")
    copy_lock = (copy_project / ".zpkg.lock").read_bytes()
    assert_projection(copy_repo, copy_project, "copy")

    copied_sentinels = [
        copy_project / "zed_modules" / "zedtest" / "ws-core" / "copy-sentinel.txt",
        copy_project
        / "node_modules"
        / "@zedtest"
        / "ws-core"
        / "copy-sentinel.txt",
    ]
    for copied_sentinel in copied_sentinels:
        if copied_sentinel.read_text(encoding="utf-8") != "before install\n":
            raise AssertionError(f"workspace copy omitted source content: {copied_sentinel}")
    copy_source_sentinel.write_text("after install\n", encoding="utf-8")
    for copied_sentinel in copied_sentinels:
        if copied_sentinel.read_text(encoding="utf-8") != "before install\n":
            raise AssertionError(
                f"workspace copy remained coupled to mutable source: {copied_sentinel}"
            )

    for projection in [
        copy_project / "zed_modules" / "zedtest" / "ws-core",
        copy_project / "node_modules" / "@zedtest" / "ws-core",
    ]:
        shutil.rmtree(projection)
    copy_repair = run(
        zed_command(
            zed,
            registry,
            home,
            "install",
            "--frozen",
            "--install-mode",
            "copy",
        ),
        cwd=copy_project,
        env=environment,
    )
    require_success(copy_repair)
    if PREFETCH_RE.search(copy_repair.output):
        raise AssertionError("frozen workspace copy repair accessed registry artifacts")
    if (copy_project / ".zpkg.lock").read_bytes() != copy_lock:
        raise AssertionError("frozen workspace copy repair rewrote the lock")
    assert_projection(copy_repo, copy_project, "copy")
    leaked = [
        candidate
        for root in (copy_project / "zed_modules", copy_project / "node_modules")
        for candidate in root.rglob("*")
        if candidate.is_symlink()
    ]
    if leaked:
        raise AssertionError(f"workspace copy mode leaked symlinks: {leaked}")

    copy_uninstall = run(
        zed_command(zed, registry, home, "uninstall"),
        cwd=copy_project,
        env=environment,
    )
    require_success(copy_uninstall)
    for projection in [
        copy_project / "zed_modules" / "zedtest" / "ws-utils",
        copy_project / "zed_modules" / "zedtest" / "ws-core",
        copy_project / "node_modules" / "@zedtest" / "ws-utils",
        copy_project / "node_modules" / "@zedtest" / "ws-core",
    ]:
        if projection.exists() or projection.is_symlink():
            raise AssertionError(f"workspace copy uninstall left projection: {projection}")
    assert_generated_wiring_absent(copy_project)
    assert_no_staging(copy_project)
    if (copy_project / ".zpkg.lock").read_bytes() != copy_lock:
        raise AssertionError("workspace copy uninstall rewrote the retained lock")

    copy_restore = run(
        zed_command(
            zed,
            registry,
            home,
            "install",
            "--frozen",
            "--install-mode",
            "copy",
        ),
        cwd=copy_project,
        env=environment,
    )
    require_success(copy_restore)
    if PREFETCH_RE.search(copy_restore.output):
        raise AssertionError("workspace copy post-uninstall restore accessed registry artifacts")
    if (copy_project / ".zpkg.lock").read_bytes() != copy_lock:
        raise AssertionError("workspace copy post-uninstall restore rewrote the lock")
    assert_projection(copy_repo, copy_project, "copy")
    assert_no_staging(copy_project)
    leaked = [
        candidate
        for root in (copy_project / "zed_modules", copy_project / "node_modules")
        for candidate in root.rglob("*")
        if candidate.is_symlink()
    ]
    if leaked:
        raise AssertionError(f"workspace restored copy mode leaked symlinks: {leaked}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    args = parser.parse_args()

    zed = args.zed.resolve()
    root = args.work_root.resolve()
    if not zed.is_file():
        raise RuntimeError(f"zed binary does not exist: {zed}")
    if root.exists():
        raise RuntimeError(f"work root must be fresh: {root}")

    registry = root / "registry"
    publish_home = root / "publish-home"
    shared_home = root / "shared-home"
    clone_root = root / "clones"
    app_root = root / "apps"
    workspace_root = root / "workspace"
    for directory in (registry, clone_root, app_root, workspace_root):
        directory.mkdir(parents=True, exist_ok=True)

    environment = os.environ.copy()
    environment.update(
        {
            "CI": "true",
            "GIT_TERMINAL_PROMPT": "0",
            "RUST_BACKTRACE": "1",
            "ZED_PKG_INSTALL_CONCURRENCY": "5",
        }
    )

    clones: dict[str, Path] = {}
    published_sources: set[tuple[str, str]] = set()
    total_downloaded = 0
    unique_shas: set[str] = set()
    per_app: dict[str, int] = {}

    for repo in APP_REPOS:
        downloaded, shas = exercise_app(
            repo,
            zed=zed,
            registry=registry,
            shared_home=shared_home,
            clone_root=clone_root,
            app_root=app_root,
            clones=clones,
            published_sources=published_sources,
            environment=environment,
        )
        total_downloaded += downloaded
        unique_shas.update(shas)
        per_app[repo] = downloaded

    if total_downloaded != len(unique_shas):
        raise AssertionError(
            "shared-home app installs did not download each immutable hash exactly once: "
            f"downloaded={total_downloaded}, unique_shas={len(unique_shas)}, per_app={per_app}"
        )

    exercise_workspace(
        zed=zed,
        registry=registry,
        home=shared_home,
        clone_root=clone_root,
        workspace_root=workspace_root,
        clones=clones,
        environment=environment,
    )

    cache_files = sorted((shared_home / "cache").glob("*.tar.gz"))
    if len(cache_files) != len(unique_shas):
        raise AssertionError(
            f"shared cache cardinality mismatch: {len(cache_files)} != {len(unique_shas)}"
        )
    temporary = sorted((shared_home / "cache").glob("**/artifact.download"))
    cache_directories = sorted(
        path for path in (shared_home / "cache").iterdir() if path.is_dir()
    )
    if temporary or cache_directories:
        raise AssertionError(
            f"shared cache leaked staging: downloads={temporary}, dirs={cache_directories}"
        )

    print("\nzed-pkg-test app install matrix passed", flush=True)
    print(f"apps: {', '.join(APP_REPOS)}", flush=True)
    print(f"cold downloads by app: {per_app}", flush=True)
    print(f"unique immutable artifacts: {len(unique_shas)}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001 - CI boundary must print full context.
        print(f"error: {error}", file=sys.stderr, flush=True)
        raise
