#!/usr/bin/env python3
"""Hermetic install recovery, rollback, and idempotency coverage for zed."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier
from typing import Mapping, Sequence

ORG = "zed-install-recovery"
VERSION = "1.0.0"
BASE_GRAPH: dict[str, tuple[str, ...]] = {
    "leaf-a": (),
    "leaf-b": (),
    "root": ("leaf-a", "leaf-b"),
}
PREFETCH_RE = re.compile(
    r"recursive install prefetch: (?P<resolved>\d+) package\(s\), "
    r"up to (?P<concurrency>\d+) concurrent, (?P<downloaded>\d+) downloaded"
)
PACKAGE_BLOCK_RE = re.compile(
    r"(?ms)^\[\[package\]\]\n.*?(?=^\[\[package\]\]|\Z)"
)
SHA_ASSIGNMENT_RE = re.compile(r'(?m)^sha256 = "[0-9a-f]{64}"$')


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
    timeout: int = 240,
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


def require_failure(completed: Completed, *expected_fragments: str) -> None:
    if completed.returncode == 0:
        shown = " ".join(shell_quote(value) for value in completed.command)
        raise AssertionError(f"command unexpectedly succeeded: {shown}")
    for fragment in expected_fragments:
        if fragment not in completed.output:
            raise AssertionError(
                f"failed command output did not contain {fragment!r}:\n{completed.output}"
            )


def zed_command(zed: Path, registry: Path, home: Path, *args: str) -> list[str | Path]:
    return [
        zed,
        "--registry",
        f"file://{registry}",
        "--home",
        home,
        *args,
    ]


def write_package(root: Path, name: str, dependencies: Sequence[str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    lines = [
        "[package]",
        f'org = "{ORG}"',
        f'name = "{name}"',
        f'version = "{VERSION}"',
        'description = "install recovery fixture"',
        "",
        "[package.repository]",
        'vcs = "git"',
        'url = "https://github.com/zed-pkg-test/zed-pkg-e2e"',
    ]
    if dependencies:
        lines.extend(("", "[dependencies]"))
        for dependency in dependencies:
            lines.append(f'"{ORG}/{dependency}" = "={VERSION}"')
    (root / ".zpkg.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (root / "payload.txt").write_text(f"{ORG}/{name}@{VERSION}\n", encoding="utf-8")


def write_consumer(root: Path, dependencies: Sequence[str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    lines = [
        "[package]",
        'org = "install-recovery-consumer"',
        f'name = "{root.name}"',
        'version = "0.1.0"',
        "",
        "[package.repository]",
        'vcs = "git"',
        'url = "https://example.invalid/install-recovery-consumer"',
        "",
        "[dependencies]",
    ]
    for dependency in dependencies:
        lines.append(f'"{ORG}/{dependency}" = "={VERSION}"')
    lines.append("")
    (root / ".zpkg.toml").write_text("\n".join(lines), encoding="utf-8")


def manifest_dependencies(project: Path) -> set[str]:
    with (project / ".zpkg.toml").open("rb") as handle:
        document = tomllib.load(handle)
    return set((document.get("dependencies") or {}).keys())


def lock_packages(project: Path) -> list[dict[str, object]]:
    with (project / ".zpkg.lock").open("rb") as handle:
        document = tomllib.load(handle)
    packages = document.get("package") or []
    if not isinstance(packages, list):
        raise AssertionError("lockfile package table is not a list")
    return packages


def lock_keys(project: Path) -> set[str]:
    return {
        f"{package['org']}/{package['name']}"
        for package in lock_packages(project)
    }


def expected_keys(names: Sequence[str]) -> set[str]:
    return {f"{ORG}/{name}" for name in names}


def assert_lock(project: Path, names: Sequence[str]) -> None:
    actual = lock_keys(project)
    expected = expected_keys(names)
    if actual != expected:
        raise AssertionError(
            f"lock graph mismatch for {project}: expected {sorted(expected)}, got {sorted(actual)}"
        )


def assert_symlink_install(
    project: Path,
    home: Path,
    names: Sequence[str],
) -> dict[str, Path]:
    store = (home / "store").resolve()
    targets: dict[str, Path] = {}
    for name in names:
        installed = project / "zed_modules" / ORG / name
        if not installed.is_symlink():
            raise AssertionError(f"expected symlink materialization: {installed}")
        target = installed.resolve(strict=True)
        try:
            target.relative_to(store)
        except ValueError as error:
            raise AssertionError(f"{installed} resolves outside {store}: {target}") from error
        payload = (target / "payload.txt").read_text(encoding="utf-8")
        if payload != f"{ORG}/{name}@{VERSION}\n":
            raise AssertionError(f"unexpected payload through {installed}: {payload!r}")
        targets[name] = target
    return targets


def assert_copy_install(project: Path, names: Sequence[str]) -> None:
    modules = project / "zed_modules"
    for name in names:
        installed = modules / ORG / name
        if installed.is_symlink() or not installed.is_dir():
            raise AssertionError(f"expected copied package directory: {installed}")
        payload = (installed / "payload.txt").read_text(encoding="utf-8")
        if payload != f"{ORG}/{name}@{VERSION}\n":
            raise AssertionError(f"unexpected copied payload in {installed}: {payload!r}")
    leaked = [path for path in modules.rglob("*") if path.is_symlink()]
    if leaked:
        raise AssertionError(f"copy install leaked symlinks: {leaked}")


def assert_not_materialized(project: Path, names: Sequence[str]) -> None:
    for name in names:
        installed = project / "zed_modules" / ORG / name
        if installed.exists() or installed.is_symlink():
            raise AssertionError(f"unexpected materialized package: {installed}")


def assert_no_transaction_leaks(project: Path) -> None:
    staging = project / ".zpkg-staging"
    if staging.exists():
        entries = list(staging.iterdir()) if staging.is_dir() else [staging]
        raise AssertionError(f"transaction staging leaked in {project}: {entries}")


def assert_no_cache_staging(home: Path) -> None:
    cache = home / "cache"
    if not cache.exists():
        return
    leaked_directories = sorted(path for path in cache.iterdir() if path.is_dir())
    leaked_downloads = sorted(cache.glob("**/artifact.download"))
    if leaked_directories or leaked_downloads:
        raise AssertionError(
            f"cache staging leaked in {home}: dirs={leaked_directories}, downloads={leaked_downloads}"
        )


def parse_prefetch(output: str) -> tuple[int, int, int]:
    matches = list(PREFETCH_RE.finditer(output))
    if len(matches) != 1:
        raise AssertionError(
            f"expected one recursive prefetch summary, got {len(matches)}:\n{output}"
        )
    match = matches[0]
    return tuple(
        int(match.group(field))
        for field in ("resolved", "concurrency", "downloaded")
    )


def assert_prefetch(
    completed: Completed,
    *,
    resolved: int,
    downloaded: int,
) -> tuple[int, int, int]:
    actual = parse_prefetch(completed.output)
    expected = (resolved, 5, downloaded)
    if actual != expected:
        raise AssertionError(f"prefetch mismatch: expected {expected}, got {actual}")
    return actual


def registry_version_path(registry: Path, name: str) -> Path:
    return registry / "packages" / ORG / name / "versions" / f"{VERSION}.json"


def registry_artifact(registry: Path, name: str) -> tuple[str, Path]:
    metadata_path = registry_version_path(registry, name)
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    sha256 = str(payload["sha256"])
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise AssertionError(f"invalid registry SHA for {name}: {sha256!r}")
    artifact = registry / "artifacts" / f"{sha256}.tar.gz"
    if not artifact.is_file():
        raise AssertionError(f"registry artifact missing before test: {artifact}")
    return sha256, artifact


def tamper_first_sha(lock_text: str) -> str:
    tampered, count = SHA_ASSIGNMENT_RE.subn(
        'sha256 = "' + ("0" * 64) + '"',
        lock_text,
        count=1,
    )
    if count != 1:
        raise AssertionError("could not locate first SHA assignment in lockfile")
    return tampered


def duplicate_first_package(lock_text: str) -> str:
    match = PACKAGE_BLOCK_RE.search(lock_text)
    if match is None:
        raise AssertionError("could not locate a package block in lockfile")
    return lock_text.rstrip() + "\n\n" + match.group(0).rstrip() + "\n"


def publish(
    zed: Path,
    registry: Path,
    home: Path,
    source: Path,
    env: Mapping[str, str],
) -> None:
    completed = run(
        zed_command(zed, registry, home, "publish", "--skip-vcs-checks"),
        cwd=source,
        env=env,
    )
    require_success(completed)


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
    copy_home = root / "copy-home"
    tamper_home = root / "tamper-home"
    duplicate_home = root / "duplicate-home"
    corrupt_home = root / "corrupt-home"
    sources = root / "sources"
    project = root / "same-project"
    copy_project = root / "copy-project"
    tamper_project = root / "tamper-project"
    duplicate_project = root / "duplicate-project"
    corrupt_project = root / "corrupt-project"
    registry.mkdir(parents=True)

    environment = os.environ.copy()
    environment.update(
        {
            "CI": "true",
            "GIT_TERMINAL_PROMPT": "0",
            "RUST_BACKTRACE": "1",
            "ZED_PKG_INSTALL_CONCURRENCY": "5",
        }
    )

    for name, dependencies in BASE_GRAPH.items():
        source = sources / name
        write_package(source, name, dependencies)
        publish(zed, registry, publish_home, source, environment)

    write_consumer(project, ("root",))
    start = Barrier(3)

    def concurrent_install() -> Completed:
        start.wait()
        return run(
            zed_command(zed, registry, shared_home, "install", "--adapter", "none"),
            cwd=project,
            env=environment,
            timeout=300,
        )

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="zed-same-project") as executor:
        futures = [executor.submit(concurrent_install) for _ in range(2)]
        start.wait()
        concurrent = [future.result(timeout=360) for future in futures]

    for completed in concurrent:
        require_success(completed)
    summaries = [parse_prefetch(completed.output) for completed in concurrent]
    if any(summary[:2] != (len(BASE_GRAPH), 5) for summary in summaries):
        raise AssertionError(f"same-project resolver summaries are wrong: {summaries}")
    if sum(summary[2] for summary in summaries) != len(BASE_GRAPH):
        raise AssertionError(
            f"same-project processes did not deduplicate downloads: {summaries}"
        )

    all_base = tuple(BASE_GRAPH)
    assert_lock(project, all_base)
    initial_targets = assert_symlink_install(project, shared_home, all_base)
    assert_no_transaction_leaks(project)
    assert_no_cache_staging(shared_home)
    lock_before = (project / ".zpkg.lock").read_bytes()
    manifest_before = (project / ".zpkg.toml").read_bytes()

    warm = run(
        zed_command(zed, registry, shared_home, "install", "--adapter", "none"),
        cwd=project,
        env=environment,
    )
    require_success(warm)
    assert_prefetch(warm, resolved=len(BASE_GRAPH), downloaded=0)
    if (project / ".zpkg.lock").read_bytes() != lock_before:
        raise AssertionError("warm ordinary install changed lockfile bytes")
    if assert_symlink_install(project, shared_home, all_base) != initial_targets:
        raise AssertionError("warm ordinary install changed immutable store targets")

    broken = project / "zed_modules" / ORG / "leaf-a"
    broken.unlink()
    repair = run(
        zed_command(
            zed,
            registry,
            shared_home,
            "install",
            "--frozen",
            "--adapter",
            "none",
        ),
        cwd=project,
        env=environment,
    )
    require_success(repair)
    assert_prefetch(repair, resolved=len(BASE_GRAPH), downloaded=0)
    if (project / ".zpkg.lock").read_bytes() != lock_before:
        raise AssertionError("frozen repair changed lockfile bytes")
    assert_symlink_install(project, shared_home, all_base)

    selective_uninstall = run(
        zed_command(zed, registry, shared_home, "uninstall", f"{ORG}/root"),
        cwd=project,
        env=environment,
    )
    require_success(selective_uninstall)
    assert_not_materialized(project, ("root",))
    assert_symlink_install(project, shared_home, ("leaf-a", "leaf-b"))
    if (project / ".zpkg.lock").read_bytes() != lock_before:
        raise AssertionError("selective uninstall changed lockfile bytes")
    selective_restore = run(
        zed_command(
            zed,
            registry,
            shared_home,
            "install",
            "--frozen",
            "--adapter",
            "none",
        ),
        cwd=project,
        env=environment,
    )
    require_success(selective_restore)
    assert_prefetch(selective_restore, resolved=len(BASE_GRAPH), downloaded=0)
    assert_symlink_install(project, shared_home, all_base)

    uninstall_all = run(
        zed_command(zed, registry, shared_home, "uninstall"),
        cwd=project,
        env=environment,
    )
    require_success(uninstall_all)
    assert_not_materialized(project, all_base)
    if (project / ".zpkg.lock").read_bytes() != lock_before:
        raise AssertionError("full uninstall changed lockfile bytes")
    if (project / ".zpkg.toml").read_bytes() != manifest_before:
        raise AssertionError("full uninstall changed manifest bytes")
    full_restore = run(
        zed_command(
            zed,
            registry,
            shared_home,
            "install",
            "--frozen",
            "--adapter",
            "none",
        ),
        cwd=project,
        env=environment,
    )
    require_success(full_restore)
    assert_prefetch(full_restore, resolved=len(BASE_GRAPH), downloaded=0)
    assert_symlink_install(project, shared_home, all_base)

    write_consumer(copy_project, ("root",))
    copy_install = run(
        zed_command(
            zed,
            registry,
            copy_home,
            "install",
            "--install-mode",
            "copy",
            "--adapter",
            "none",
        ),
        cwd=copy_project,
        env=environment,
    )
    require_success(copy_install)
    assert_prefetch(copy_install, resolved=len(BASE_GRAPH), downloaded=len(BASE_GRAPH))
    assert_lock(copy_project, all_base)
    assert_copy_install(copy_project, all_base)
    copy_lock = (copy_project / ".zpkg.lock").read_bytes()
    copy_warm = run(
        zed_command(
            zed,
            registry,
            copy_home,
            "install",
            "--frozen",
            "--install-mode",
            "copy",
            "--adapter",
            "none",
        ),
        cwd=copy_project,
        env=environment,
    )
    require_success(copy_warm)
    assert_prefetch(copy_warm, resolved=len(BASE_GRAPH), downloaded=0)
    if (copy_project / ".zpkg.lock").read_bytes() != copy_lock:
        raise AssertionError("warm copy-mode frozen install changed lockfile bytes")
    assert_copy_install(copy_project, all_base)

    good_lock_text = (project / ".zpkg.lock").read_text(encoding="utf-8")
    write_consumer(tamper_project, ("root",))
    (tamper_project / ".zpkg.lock").write_text(
        tamper_first_sha(good_lock_text), encoding="utf-8"
    )
    tampered_before = (tamper_project / ".zpkg.lock").read_bytes()
    tampered = run(
        zed_command(
            zed,
            registry,
            tamper_home,
            "install",
            "--frozen",
            "--adapter",
            "none",
        ),
        cwd=tamper_project,
        env=environment,
    )
    require_failure(tampered, "registry artifact", "changed")
    if (tamper_project / ".zpkg.lock").read_bytes() != tampered_before:
        raise AssertionError("failed tampered-lock install changed lockfile bytes")
    assert_not_materialized(tamper_project, all_base)
    assert_no_transaction_leaks(tamper_project)
    assert_no_cache_staging(tamper_home)

    write_consumer(duplicate_project, ("root",))
    (duplicate_project / ".zpkg.lock").write_text(
        duplicate_first_package(good_lock_text), encoding="utf-8"
    )
    duplicate_before = (duplicate_project / ".zpkg.lock").read_bytes()
    duplicate = run(
        zed_command(
            zed,
            registry,
            duplicate_home,
            "install",
            "--frozen",
            "--adapter",
            "none",
        ),
        cwd=duplicate_project,
        env=environment,
    )
    require_failure(duplicate, "duplicate package")
    if (duplicate_project / ".zpkg.lock").read_bytes() != duplicate_before:
        raise AssertionError("failed duplicate-lock install changed lockfile bytes")
    assert_not_materialized(duplicate_project, all_base)
    assert_no_transaction_leaks(duplicate_project)

    # A new direct dependency whose registry artifact is unavailable must not
    # damage the already-installed graph or rewrite its last known-good lock.
    extra_source = sources / "extra"
    write_package(extra_source, "extra", ())
    publish(zed, registry, publish_home, extra_source, environment)
    extra_sha, extra_artifact = registry_artifact(registry, "extra")
    extra_bytes = extra_artifact.read_bytes()
    extra_artifact.unlink()
    write_consumer(project, ("root", "extra"))
    preserved_targets = assert_symlink_install(project, shared_home, all_base)
    unavailable = run(
        zed_command(zed, registry, shared_home, "install", "--adapter", "none"),
        cwd=project,
        env=environment,
    )
    require_failure(unavailable)
    if (project / ".zpkg.lock").read_bytes() != lock_before:
        raise AssertionError("missing-artifact failure replaced the good lockfile")
    if assert_symlink_install(project, shared_home, all_base) != preserved_targets:
        raise AssertionError("missing-artifact failure changed the installed graph")
    assert_not_materialized(project, ("extra",))
    assert_no_transaction_leaks(project)
    assert_no_cache_staging(shared_home)
    if (shared_home / "cache" / f"{extra_sha}.tar.gz").exists():
        raise AssertionError("missing artifact produced a final cache entry")

    extra_artifact.write_bytes(extra_bytes)
    recovered = run(
        zed_command(zed, registry, shared_home, "install", "--adapter", "none"),
        cwd=project,
        env=environment,
    )
    require_success(recovered)
    assert_prefetch(recovered, resolved=len(BASE_GRAPH) + 1, downloaded=1)
    extended = (*all_base, "extra")
    assert_lock(project, extended)
    assert_symlink_install(project, shared_home, extended)
    assert_no_cache_staging(shared_home)

    # Corrupt bytes must fail before project mutation, leave no final cache
    # entry for the bad hash, then recover by downloading exactly that hash.
    write_consumer(corrupt_project, ("root",))
    corrupt_sha, corrupt_artifact = registry_artifact(registry, "leaf-b")
    corrupt_bytes = corrupt_artifact.read_bytes()
    corrupt_artifact.write_bytes(b"not a valid zed artifact\n")
    corrupt = run(
        zed_command(zed, registry, corrupt_home, "install", "--adapter", "none"),
        cwd=corrupt_project,
        env=environment,
    )
    require_failure(corrupt, "artifact hash mismatch")
    if (corrupt_project / ".zpkg.lock").exists():
        raise AssertionError("corrupt-artifact failure wrote a lockfile")
    assert_not_materialized(corrupt_project, all_base)
    assert_no_transaction_leaks(corrupt_project)
    assert_no_cache_staging(corrupt_home)
    if (corrupt_home / "cache" / f"{corrupt_sha}.tar.gz").exists():
        raise AssertionError("corrupt artifact was published to the final cache path")

    corrupt_artifact.write_bytes(corrupt_bytes)
    corrupt_recovery = run(
        zed_command(zed, registry, corrupt_home, "install", "--adapter", "none"),
        cwd=corrupt_project,
        env=environment,
    )
    require_success(corrupt_recovery)
    recovery_summary = parse_prefetch(corrupt_recovery.output)
    if recovery_summary[:2] != (len(BASE_GRAPH), 5) or recovery_summary[2] < 1:
        raise AssertionError(f"unexpected corrupt-artifact recovery summary: {recovery_summary}")
    assert_lock(corrupt_project, all_base)
    assert_symlink_install(corrupt_project, corrupt_home, all_base)
    assert_no_cache_staging(corrupt_home)

    print("\ninstall recovery matrix passed", flush=True)
    print(f"same-project concurrent summaries: {summaries}", flush=True)
    print(f"corrupt recovery summary: {recovery_summary}", flush=True)
    print(f"extended manifest dependencies: {sorted(manifest_dependencies(project))}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001 - CI boundary must print full context.
        print(f"error: {error}", file=sys.stderr, flush=True)
        raise
