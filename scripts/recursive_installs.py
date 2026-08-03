#!/usr/bin/env python3
"""Hermetic end-to-end coverage for recursive zed installs and shared-home locks."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier
from typing import Mapping, Sequence

ORG = "zed-recursive-test"
VERSION = "1.0.0"
PACKAGE_GRAPH: dict[str, tuple[str, ...]] = {
    "leaf": (),
    "left": ("leaf",),
    "right": ("leaf",),
    "root": ("left", "right"),
}
PREFETCH_RE = re.compile(
    r"recursive install prefetch: (?P<resolved>\d+) package\(s\), "
    r"up to (?P<concurrency>\d+) concurrent, (?P<downloaded>\d+) downloaded"
)


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
        timeout=180,
    )
    if completed.stdout:
        print(completed.stdout, end="", flush=True)
    return Completed(argv, completed.returncode, completed.stdout)


def require_success(completed: Completed) -> None:
    if completed.returncode != 0:
        shown = " ".join(shell_quote(value) for value in completed.command)
        raise RuntimeError(f"command failed with exit code {completed.returncode}: {shown}")


def write_package(root: Path, name: str, dependencies: Sequence[str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    lines = [
        "[package]",
        f'org = "{ORG}"',
        f'name = "{name}"',
        f'version = "{VERSION}"',
        'description = "recursive install e2e fixture"',
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
        'org = "recursive-consumer"',
        f'name = "{root.name}"',
        'version = "0.1.0"',
        "",
        "[package.repository]",
        'vcs = "git"',
        'url = "https://example.invalid/recursive-consumer"',
        "",
        "[dependencies]",
    ]
    for dependency in dependencies:
        lines.append(f'"{ORG}/{dependency}" = "={VERSION}"')
    lines.append("")
    (root / ".zpkg.toml").write_text("\n".join(lines), encoding="utf-8")


def zed_command(zed: Path, registry: Path, home: Path, *args: str) -> list[str | Path]:
    return [
        zed,
        "--registry",
        f"file://{registry}",
        "--home",
        home,
        *args,
    ]


def package_keys(names: Sequence[str]) -> set[str]:
    return {f"{ORG}/{name}" for name in names}


def assert_manifest_dependencies(consumer: Path, expected_names: Sequence[str]) -> None:
    with (consumer / ".zpkg.toml").open("rb") as handle:
        document = tomllib.load(handle)
    actual = set((document.get("dependencies") or {}).keys())
    expected = package_keys(expected_names)
    if actual != expected:
        raise AssertionError(
            f"manifest dependency mismatch: expected {sorted(expected)}, got {sorted(actual)}"
        )


def assert_lock_packages(consumer: Path, expected_names: Sequence[str]) -> None:
    with (consumer / ".zpkg.lock").open("rb") as handle:
        document = tomllib.load(handle)
    packages = document.get("package") or []
    actual = {f"{package['org']}/{package['name']}" for package in packages}
    expected = package_keys(expected_names)
    if actual != expected:
        raise AssertionError(
            f"lock graph mismatch: expected {sorted(expected)}, got {sorted(actual)}"
        )


def assert_symlink_packages(
    consumer: Path,
    store_root: Path,
    expected_names: Sequence[str],
) -> dict[str, Path]:
    targets: dict[str, Path] = {}
    for name in expected_names:
        installed = consumer / "zed_modules" / ORG / name
        if not installed.is_symlink():
            raise AssertionError(f"default install did not symlink {installed}")
        target = installed.resolve(strict=True)
        try:
            target.relative_to(store_root)
        except ValueError as error:
            raise AssertionError(
                f"{installed} resolves outside the shared store: {target}"
            ) from error
        if (target / "payload.txt").read_text(encoding="utf-8") != f"{ORG}/{name}@{VERSION}\n":
            raise AssertionError(f"unexpected payload for {installed}")
        targets[name] = target
    return targets


def assert_not_materialized(consumer: Path, names: Sequence[str]) -> None:
    for name in names:
        installed = consumer / "zed_modules" / ORG / name
        if installed.exists() or installed.is_symlink():
            raise AssertionError(f"unexpected materialized package {installed}")


def assert_artifact_home(home: Path, expected_count: int) -> None:
    cache_files = sorted((home / "cache").glob("*.tar.gz"))
    if len(cache_files) != expected_count:
        raise AssertionError(
            f"expected {expected_count} cached artifacts in {home}, got {cache_files}"
        )
    temporary_downloads = list((home / "cache").glob("**/artifact.download"))
    if temporary_downloads:
        raise AssertionError(
            f"temporary downloads leaked into {home}: {temporary_downloads}"
        )
    lock_files = sorted((home / "locks").glob("artifact-*.lock"))
    if len(lock_files) != expected_count:
        raise AssertionError(
            f"expected {expected_count} artifact lock files in {home}, got {lock_files}"
        )


def parse_prefetch(output: str) -> tuple[int, int, int]:
    matches = list(PREFETCH_RE.finditer(output))
    if len(matches) != 1:
        raise AssertionError(
            f"expected one recursive prefetch summary, got {len(matches)}\n{output}"
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
    summary = parse_prefetch(completed.output)
    expected = (resolved, 5, downloaded)
    if summary != expected:
        raise AssertionError(f"prefetch summary mismatch: expected {expected}, got {summary}")
    return summary


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
    add_home = root / "add-home"
    remove_home = root / "remove-home"
    sources = root / "sources"
    consumers = [root / "consumer-a", root / "consumer-b"]
    add_consumer = root / "consumer-add"
    remove_consumer = root / "consumer-remove"
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

    for name, dependencies in PACKAGE_GRAPH.items():
        package_root = sources / name
        write_package(package_root, name, dependencies)
        completed = run(
            zed_command(
                zed,
                registry,
                publish_home,
                "publish",
                "--skip-vcs-checks",
            ),
            cwd=package_root,
            env=environment,
        )
        require_success(completed)

    for consumer in consumers:
        write_consumer(consumer, ("root",))

    start = Barrier(3)

    def install(consumer: Path) -> Completed:
        start.wait()
        return run(
            zed_command(
                zed,
                registry,
                shared_home,
                "install",
                "--adapter",
                "none",
            ),
            cwd=consumer,
            env=environment,
        )

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="zed-e2e") as executor:
        futures = [executor.submit(install, consumer) for consumer in consumers]
        start.wait()
        completed_installs = [future.result(timeout=240) for future in futures]

    for completed in completed_installs:
        require_success(completed)

    summaries = [parse_prefetch(completed.output) for completed in completed_installs]
    if any(resolved != len(PACKAGE_GRAPH) for resolved, _, _ in summaries):
        raise AssertionError(
            f"each recursive resolver must discover four packages: {summaries}"
        )
    if any(concurrency != 5 for _, concurrency, _ in summaries):
        raise AssertionError(
            f"installer did not honor five-worker concurrency: {summaries}"
        )
    if sum(downloaded for _, _, downloaded in summaries) != len(PACKAGE_GRAPH):
        raise AssertionError(
            "shared-home installs did not deduplicate downloads across processes: "
            f"{summaries}"
        )

    first_targets: dict[str, Path] | None = None
    store_root = (shared_home / "store").resolve()
    all_packages = tuple(PACKAGE_GRAPH)
    for consumer in consumers:
        assert_manifest_dependencies(consumer, ("root",))
        assert_lock_packages(consumer, all_packages)
        targets = assert_symlink_packages(consumer, store_root, all_packages)
        if first_targets is None:
            first_targets = targets
        elif targets != first_targets:
            raise AssertionError(
                f"consumers did not share identical store entries: {first_targets} != {targets}"
            )
    assert_artifact_home(shared_home, len(PACKAGE_GRAPH))

    # `zed add` installs against an in-memory proposed manifest before writing
    # it. This must enter the same recursive prefetch facade as a normal install.
    write_consumer(add_consumer, ())
    add_completed = run(
        zed_command(
            zed,
            registry,
            add_home,
            "add",
            f"{ORG}/root@={VERSION}",
        ),
        cwd=add_consumer,
        env=environment,
    )
    require_success(add_completed)
    add_summary = assert_prefetch(
        add_completed,
        resolved=len(PACKAGE_GRAPH),
        downloaded=len(PACKAGE_GRAPH),
    )
    assert_manifest_dependencies(add_consumer, ("root",))
    assert_lock_packages(add_consumer, all_packages)
    assert_symlink_packages(add_consumer, (add_home / "store").resolve(), all_packages)
    assert_artifact_home(add_home, len(PACKAGE_GRAPH))

    # `zed remove` also installs an in-memory proposed manifest. Starting with
    # root + left and removing root leaves left -> leaf, so exactly two packages
    # should be recursively prefetched and materialized.
    write_consumer(remove_consumer, ("root", "left"))
    remove_completed = run(
        zed_command(
            zed,
            registry,
            remove_home,
            "remove",
            f"{ORG}/root",
        ),
        cwd=remove_consumer,
        env=environment,
    )
    require_success(remove_completed)
    remove_summary = assert_prefetch(remove_completed, resolved=2, downloaded=2)
    assert_manifest_dependencies(remove_consumer, ("left",))
    assert_lock_packages(remove_consumer, ("left", "leaf"))
    assert_symlink_packages(
        remove_consumer,
        (remove_home / "store").resolve(),
        ("left", "leaf"),
    )
    assert_not_materialized(remove_consumer, ("root", "right"))
    assert_artifact_home(remove_home, 2)

    print("\nrecursive install e2e passed", flush=True)
    print(f"concurrent install summaries: {summaries}", flush=True)
    print(f"add summary: {add_summary}", flush=True)
    print(f"remove summary: {remove_summary}", flush=True)
    print(f"shared store: {store_root}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001 - CI entry point must print context.
        print(f"error: {error}", file=sys.stderr, flush=True)
        raise
