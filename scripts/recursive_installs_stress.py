#!/usr/bin/env python3
"""Stress recursive zed installs across processes and a manifestless frozen replay."""

from __future__ import annotations

import argparse
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

ORG = "zed-recursive-stress"
VERSION = "1.0.0"
LEAVES = tuple(f"leaf-{index:02d}" for index in range(12))
ROOT = "root"
ALL_PACKAGES = (*LEAVES, ROOT)
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
        timeout=240,
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
        'description = "recursive install stress fixture"',
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


def write_consumer(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".zpkg.toml").write_text(
        "\n".join(
            (
                "[package]",
                'org = "recursive-stress-consumer"',
                f'name = "{root.name}"',
                'version = "0.1.0"',
                "",
                "[package.repository]",
                'vcs = "git"',
                'url = "https://example.invalid/recursive-stress-consumer"',
                "",
                "[dependencies]",
                f'"{ORG}/{ROOT}" = "={VERSION}"',
                "",
            )
        ),
        encoding="utf-8",
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


def package_keys(names: Sequence[str]) -> set[str]:
    return {f"{ORG}/{name}" for name in names}


def assert_manifest_root_only(consumer: Path) -> None:
    with (consumer / ".zpkg.toml").open("rb") as handle:
        document = tomllib.load(handle)
    actual = set((document.get("dependencies") or {}).keys())
    expected = package_keys((ROOT,))
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
        payload = (target / "payload.txt").read_text(encoding="utf-8")
        if payload != f"{ORG}/{name}@{VERSION}\n":
            raise AssertionError(f"unexpected payload for {installed}: {payload!r}")
        targets[name] = target
    return targets


def assert_artifact_home(home: Path, expected_count: int) -> None:
    cache = home / "cache"
    cache_files = sorted(cache.glob("*.tar.gz"))
    if len(cache_files) != expected_count:
        raise AssertionError(
            f"expected {expected_count} cached artifacts in {home}, got {cache_files}"
        )
    leaked_directories = sorted(path for path in cache.iterdir() if path.is_dir())
    if leaked_directories:
        raise AssertionError(f"temporary cache directories leaked: {leaked_directories}")
    temporary_downloads = list(cache.glob("**/artifact.download"))
    if temporary_downloads:
        raise AssertionError(f"temporary downloads leaked: {temporary_downloads}")
    lock_files = sorted((home / "locks").glob("artifact-*.lock"))
    if len(lock_files) != expected_count:
        raise AssertionError(
            f"expected {expected_count} artifact locks in {home}, got {lock_files}"
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
    frozen_home = root / "frozen-home"
    sources = root / "sources"
    consumers = [root / f"consumer-{index}" for index in range(4)]
    frozen_consumer = root / "consumer-frozen"
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

    for name in LEAVES:
        package_root = sources / name
        write_package(package_root, name, ())
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

    root_package = sources / ROOT
    write_package(root_package, ROOT, LEAVES)
    root_publish = run(
        zed_command(
            zed,
            registry,
            publish_home,
            "publish",
            "--skip-vcs-checks",
        ),
        cwd=root_package,
        env=environment,
    )
    require_success(root_publish)

    for consumer in consumers:
        write_consumer(consumer)

    start = Barrier(len(consumers) + 1)

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

    with ThreadPoolExecutor(
        max_workers=len(consumers),
        thread_name_prefix="zed-recursive-stress",
    ) as executor:
        futures = [executor.submit(install, consumer) for consumer in consumers]
        start.wait()
        installs = [future.result(timeout=300) for future in futures]

    for completed in installs:
        require_success(completed)

    summaries = [parse_prefetch(completed.output) for completed in installs]
    expected_count = len(ALL_PACKAGES)
    if any(resolved != expected_count for resolved, _, _ in summaries):
        raise AssertionError(f"every process must resolve the complete graph: {summaries}")
    if any(concurrency != 5 for _, concurrency, _ in summaries):
        raise AssertionError(f"five-worker configuration was not honored: {summaries}")
    if sum(downloaded for _, _, downloaded in summaries) != expected_count:
        raise AssertionError(
            "four processes did not deduplicate shared-home downloads: "
            f"{summaries}"
        )

    first_targets: dict[str, Path] | None = None
    shared_store = (shared_home / "store").resolve()
    for consumer in consumers:
        assert_manifest_root_only(consumer)
        assert_lock_packages(consumer, ALL_PACKAGES)
        targets = assert_symlink_packages(consumer, shared_store, ALL_PACKAGES)
        if first_targets is None:
            first_targets = targets
        elif targets != first_targets:
            raise AssertionError(
                f"consumers did not share identical store entries: {first_targets} != {targets}"
            )
    assert_artifact_home(shared_home, expected_count)

    # Replay the complete transitive lockfile in a fresh project with no
    # manifest. This exercises the lock-only recursive prefetch facade and its
    # normal symlink materialization path.
    frozen_consumer.mkdir(parents=True)
    source_lock = consumers[0] / ".zpkg.lock"
    frozen_lock = frozen_consumer / ".zpkg.lock"
    shutil.copyfile(source_lock, frozen_lock)
    lock_before = frozen_lock.read_bytes()
    frozen_install = run(
        zed_command(
            zed,
            registry,
            frozen_home,
            "install",
            "--frozen",
            "--allow-no-manifest",
            "--adapter",
            "none",
        ),
        cwd=frozen_consumer,
        env=environment,
    )
    require_success(frozen_install)
    frozen_summary = assert_prefetch(
        frozen_install,
        resolved=expected_count,
        downloaded=expected_count,
    )
    if (frozen_consumer / ".zpkg.toml").exists():
        raise AssertionError("manifestless frozen install wrote a new manifest")
    if frozen_lock.read_bytes() != lock_before:
        raise AssertionError("frozen install modified the lockfile")
    assert_lock_packages(frozen_consumer, ALL_PACKAGES)
    assert_symlink_packages(
        frozen_consumer,
        (frozen_home / "store").resolve(),
        ALL_PACKAGES,
    )
    assert_artifact_home(frozen_home, expected_count)

    frozen_warm = run(
        zed_command(
            zed,
            registry,
            frozen_home,
            "install",
            "--frozen",
            "--allow-no-manifest",
            "--adapter",
            "none",
        ),
        cwd=frozen_consumer,
        env=environment,
    )
    require_success(frozen_warm)
    frozen_warm_summary = assert_prefetch(
        frozen_warm,
        resolved=expected_count,
        downloaded=0,
    )
    if frozen_lock.read_bytes() != lock_before:
        raise AssertionError("warm frozen install modified the lockfile")

    print("\nrecursive install stress passed", flush=True)
    print(f"concurrent process summaries: {summaries}", flush=True)
    print(f"frozen summary: {frozen_summary}", flush=True)
    print(f"warm frozen summary: {frozen_warm_summary}", flush=True)
    print(f"shared store: {shared_store}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001 - CI entry point prints full context.
        print(f"error: {error}", file=sys.stderr, flush=True)
        raise
