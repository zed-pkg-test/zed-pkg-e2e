#!/usr/bin/env python3
"""Black-box certification for zed's complete one-version dependency solver."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

ORG = "zed-solver-e2e"
PREFETCH_RE = re.compile(
    r"recursive install prefetch: (?P<resolved>\d+) package\(s\), "
    r"up to (?P<concurrency>\d+) concurrent, (?P<downloaded>\d+) downloaded"
)


@dataclass(frozen=True)
class Completed:
    command: tuple[str, ...]
    returncode: int
    output: str


@dataclass(frozen=True)
class Package:
    name: str
    version: str
    dependencies: Mapping[str, str]


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
        raise RuntimeError(
            f"command failed with exit code {completed.returncode}: {shown}\n"
            f"{completed.output}"
        )


def require_failure(completed: Completed) -> None:
    if completed.returncode == 0:
        shown = " ".join(shell_quote(value) for value in completed.command)
        raise AssertionError(f"command unexpectedly succeeded: {shown}")


def zed_command(zed: Path, registry: Path, home: Path, *args: str) -> list[str | Path]:
    return [
        zed,
        "--registry",
        f"file://{registry}",
        "--home",
        home,
        *args,
    ]


def package_key(name: str) -> str:
    return f"{ORG}/{name}"


def write_package(root: Path, package: Package) -> None:
    root.mkdir(parents=True, exist_ok=True)
    lines = [
        "[package]",
        f'org = "{ORG}"',
        f'name = "{package.name}"',
        f'version = "{package.version}"',
        'description = "complete solver black-box fixture"',
        'license = "MIT"',
        "",
        "[package.repository]",
        'vcs = "git"',
        'url = "https://github.com/zed-pkg-test/zed-pkg-e2e"',
    ]
    if package.dependencies:
        lines.extend(("", "[dependencies]"))
        for dependency, requirement in package.dependencies.items():
            lines.append(f'"{package_key(dependency)}" = "{requirement}"')
    (root / ".zpkg.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (root / "payload.txt").write_text(
        f"{package_key(package.name)}@{package.version}\n",
        encoding="utf-8",
    )


def write_consumer(
    root: Path,
    *,
    identity: str,
    dependencies: Sequence[tuple[str, str]],
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    lines = [
        "[package]",
        'org = "solver-consumer"',
        f'name = "{identity}"',
        'version = "0.1.0"',
        'description = "black-box solver consumer"',
        "",
        "[package.repository]",
        'vcs = "git"',
        'url = "https://example.invalid/solver-consumer"',
        "",
        "[dependencies]",
    ]
    for name, requirement in dependencies:
        lines.append(f'"{package_key(name)}" = "{requirement}"')
    lines.append("")
    (root / ".zpkg.toml").write_text("\n".join(lines), encoding="utf-8")


def publish(
    zed: Path,
    registry: Path,
    publish_home: Path,
    sources: Path,
    package: Package,
    environment: Mapping[str, str],
) -> None:
    safe_version = package.version.replace(".", "-").replace("+", "-")
    source = sources / f"{package.name}-{safe_version}"
    write_package(source, package)
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


def parse_prefetch(output: str) -> tuple[int, int, int]:
    matches = list(PREFETCH_RE.finditer(output))
    if len(matches) != 1:
        raise AssertionError(
            f"expected exactly one prefetch summary, got {len(matches)}:\n{output}"
        )
    match = matches[0]
    return tuple(
        int(match.group(field))
        for field in ("resolved", "concurrency", "downloaded")
    )


def lock_versions(project: Path) -> dict[str, str]:
    with (project / ".zpkg.lock").open("rb") as handle:
        document = tomllib.load(handle)
    packages = document.get("package") or []
    return {
        f"{package['org']}/{package['name']}": str(package["version"])
        for package in packages
    }


def assert_lock(project: Path, expected: Mapping[str, str]) -> None:
    actual = lock_versions(project)
    wanted = {package_key(name): version for name, version in expected.items()}
    if actual != wanted:
        raise AssertionError(
            f"lock mismatch for {project}: expected {wanted}, got {actual}"
        )


def assert_symlink_graph(
    project: Path,
    home: Path,
    expected: Mapping[str, str],
) -> dict[str, Path]:
    store = (home / "store").resolve()
    targets: dict[str, Path] = {}
    for name, version in expected.items():
        installed = project / "zed_modules" / ORG / name
        if not installed.is_symlink():
            raise AssertionError(f"expected symlink materialization: {installed}")
        target = installed.resolve(strict=True)
        try:
            target.relative_to(store)
        except ValueError as error:
            raise AssertionError(f"{installed} resolves outside {store}: {target}") from error
        payload = (target / "payload.txt").read_text(encoding="utf-8")
        wanted = f"{package_key(name)}@{version}\n"
        if payload != wanted:
            raise AssertionError(
                f"unexpected payload through {installed}: {payload!r} != {wanted!r}"
            )
        targets[name] = target
    return targets


def assert_absent(project: Path, names: Sequence[str]) -> None:
    for name in names:
        path = project / "zed_modules" / ORG / name
        if path.exists() or path.is_symlink():
            raise AssertionError(f"rejected candidate leaked into project: {path}")


def assert_no_project_mutation(project: Path) -> None:
    if (project / ".zpkg.lock").exists():
        raise AssertionError(f"failed solve wrote a lockfile: {project}")
    modules = project / "zed_modules"
    if modules.exists():
        raise AssertionError(f"failed solve materialized modules: {modules}")
    staging = project / ".zpkg-staging"
    if staging.exists():
        raise AssertionError(f"failed solve leaked transaction staging: {staging}")


def normalized_error(output: str, root: Path) -> str:
    normalized = output.replace(str(root), "<WORK_ROOT>")
    return "\n".join(line.rstrip() for line in normalized.splitlines() if line.strip())


def install(
    zed: Path,
    registry: Path,
    home: Path,
    project: Path,
    environment: Mapping[str, str],
    *extra: str,
) -> Completed:
    return run(
        zed_command(
            zed,
            registry,
            home,
            "install",
            "--adapter",
            "none",
            *extra,
        ),
        cwd=project,
        env=environment,
    )


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
    sources = root / "sources"
    registry.mkdir(parents=True)

    environment = os.environ.copy()
    environment.update(
        {
            "CI": "true",
            "GIT_TERMINAL_PROMPT": "0",
            "RUST_BACKTRACE": "1",
            "ZED_PKG_INSTALL_CONCURRENCY": "5",
            "ZED_PKG_TOKEN": "",
        }
    )

    packages = [
        # Overlapping compatible ranges: 1.5.0 is the only common solution.
        Package("overlap-shared", "1.5.0", {}),
        Package("overlap-shared", "1.9.0", {}),
        Package("overlap-left", "1.0.0", {"overlap-shared": "^1"}),
        Package("overlap-right", "1.0.0", {"overlap-shared": "<=1.5.0"}),
        # A two-coordinate backtracking graph. Latest a + latest b conflict.
        Package("backtrack-x", "1.0.0", {}),
        Package("backtrack-x", "2.0.0", {}),
        Package("backtrack-a", "1.0.0", {"backtrack-x": "=1.0.0"}),
        Package("backtrack-a", "1.1.0", {"backtrack-x": "=2.0.0"}),
        Package("backtrack-b", "1.0.0", {"backtrack-x": "=2.0.0"}),
        Package("backtrack-b", "1.1.0", {"backtrack-x": "=1.0.0"}),
        # Rejected chooser@1.1 contributes obsolete + shared@1. Those
        # constraints must disappear when chooser@1.0 is selected.
        Package("stale-shared", "1.0.0", {}),
        Package("stale-shared", "2.0.0", {}),
        Package("stale-obsolete", "1.0.0", {}),
        Package("stale-replacement", "1.0.0", {}),
        Package(
            "stale-chooser",
            "1.0.0",
            {
                "stale-replacement": "=1.0.0",
                "stale-shared": "=2.0.0",
            },
        ),
        Package(
            "stale-chooser",
            "1.1.0",
            {
                "stale-obsolete": "=1.0.0",
                "stale-shared": "=1.0.0",
            },
        ),
        Package("stale-force", "1.0.0", {"stale-shared": "=2.0.0"}),
        # Deterministic unsatisfiable provenance.
        Package("unsat-shared", "1.0.0", {}),
        Package("unsat-shared", "2.0.0", {}),
        Package("unsat-left", "1.0.0", {"unsat-shared": "=1.0.0"}),
        Package("unsat-right", "1.0.0", {"unsat-shared": "=2.0.0"}),
        # Cycle termination.
        Package("cycle-a", "1.0.0", {"cycle-b": "=1.0.0"}),
        Package("cycle-b", "1.0.0", {"cycle-a": "=1.0.0"}),
    ]
    for package in packages:
        publish(zed, registry, publish_home, sources, package, environment)

    # 1. Overlapping ranges resolve to the common older version, regardless of
    # root declaration order, and emit byte-identical lockfiles. Unresolved
    # root packages must contribute their constraints before the deeper shared
    # coordinate is acquired, so each cold home downloads only the three
    # selected artifacts and never speculates into rejected shared@1.9.0.
    overlap_expected = {
        "overlap-left": "1.0.0",
        "overlap-right": "1.0.0",
        "overlap-shared": "1.5.0",
    }
    overlap_a = root / "overlap-a"
    overlap_b = root / "overlap-b"
    write_consumer(
        overlap_a,
        identity="overlap",
        dependencies=(
            ("overlap-left", "=1.0.0"),
            ("overlap-right", "=1.0.0"),
        ),
    )
    write_consumer(
        overlap_b,
        identity="overlap",
        dependencies=(
            ("overlap-right", "=1.0.0"),
            ("overlap-left", "=1.0.0"),
        ),
    )
    overlap_home_a = root / "overlap-home-a"
    overlap_home_b = root / "overlap-home-b"
    overlap_first = install(
        zed, registry, overlap_home_a, overlap_a, environment
    )
    overlap_second = install(
        zed, registry, overlap_home_b, overlap_b, environment
    )
    require_success(overlap_first)
    require_success(overlap_second)
    if parse_prefetch(overlap_first.output) != (3, 5, 3):
        raise AssertionError(f"unexpected first overlap summary: {overlap_first.output}")
    if parse_prefetch(overlap_second.output) != (3, 5, 3):
        raise AssertionError(f"unexpected second overlap summary: {overlap_second.output}")
    assert_lock(overlap_a, overlap_expected)
    assert_lock(overlap_b, overlap_expected)
    targets = assert_symlink_graph(overlap_a, overlap_home_a, overlap_expected)
    assert_symlink_graph(overlap_b, overlap_home_b, overlap_expected)
    lock_bytes = (overlap_a / ".zpkg.lock").read_bytes()
    if (overlap_b / ".zpkg.lock").read_bytes() != lock_bytes:
        raise AssertionError("root declaration order changed lockfile bytes")

    warm = install(
        zed,
        registry,
        overlap_home_a,
        overlap_a,
        environment,
        "--frozen",
    )
    require_success(warm)
    if parse_prefetch(warm.output) != (3, 5, 0):
        raise AssertionError(f"warm frozen overlap replay downloaded bytes: {warm.output}")
    if (overlap_a / ".zpkg.lock").read_bytes() != lock_bytes:
        raise AssertionError("warm frozen replay changed overlap lock bytes")
    if assert_symlink_graph(overlap_a, overlap_home_a, overlap_expected) != targets:
        raise AssertionError("warm frozen replay changed immutable store targets")

    # Add a newer compatible version after the lock exists. Frozen replay in a
    # fresh home must consume the exact solved graph without re-solving.
    publish(
        zed,
        registry,
        publish_home,
        sources,
        Package("overlap-shared", "1.10.0", {}),
        environment,
    )
    frozen_overlap = root / "overlap-frozen"
    frozen_overlap.mkdir()
    shutil.copy2(overlap_a / ".zpkg.toml", frozen_overlap / ".zpkg.toml")
    shutil.copy2(overlap_a / ".zpkg.lock", frozen_overlap / ".zpkg.lock")
    frozen_home = root / "overlap-frozen-home"
    frozen = install(
        zed,
        registry,
        frozen_home,
        frozen_overlap,
        environment,
        "--frozen",
    )
    require_success(frozen)
    if parse_prefetch(frozen.output) != (3, 5, 3):
        raise AssertionError(f"fresh frozen replay summary mismatch: {frozen.output}")
    assert_lock(frozen_overlap, overlap_expected)
    assert_symlink_graph(frozen_overlap, frozen_home, overlap_expected)
    if (frozen_overlap / ".zpkg.lock").read_bytes() != lock_bytes:
        raise AssertionError("fresh frozen replay changed solved lock bytes")

    # 2. Latest-first search must backtrack across package b after selecting
    # package a, producing the stable a@1.1 + b@1.0 + x@2 solution.
    backtrack_project = root / "backtrack"
    write_consumer(
        backtrack_project,
        identity="backtrack",
        dependencies=(("backtrack-a", "^1"), ("backtrack-b", "^1")),
    )
    backtrack_home = root / "backtrack-home"
    backtrack = install(
        zed, registry, backtrack_home, backtrack_project, environment
    )
    require_success(backtrack)
    backtrack_summary = parse_prefetch(backtrack.output)
    if backtrack_summary[:2] != (3, 5) or backtrack_summary[2] < 3:
        raise AssertionError(f"backtracking did not produce a valid summary: {backtrack_summary}")
    backtrack_expected = {
        "backtrack-a": "1.1.0",
        "backtrack-b": "1.0.0",
        "backtrack-x": "2.0.0",
    }
    assert_lock(backtrack_project, backtrack_expected)
    assert_symlink_graph(backtrack_project, backtrack_home, backtrack_expected)

    # 3. Constraints and dependencies contributed by a rejected candidate must
    # not leak into the final graph.
    stale_project = root / "stale"
    write_consumer(
        stale_project,
        identity="stale",
        dependencies=(("stale-chooser", "^1"), ("stale-force", "=1.0.0")),
    )
    stale_home = root / "stale-home"
    stale = install(zed, registry, stale_home, stale_project, environment)
    require_success(stale)
    stale_expected = {
        "stale-chooser": "1.0.0",
        "stale-force": "1.0.0",
        "stale-replacement": "1.0.0",
        "stale-shared": "2.0.0",
    }
    assert_lock(stale_project, stale_expected)
    assert_symlink_graph(stale_project, stale_home, stale_expected)
    assert_absent(stale_project, ("stale-obsolete",))

    # 4. An actually unsatisfiable graph reports both provenance paths in a
    # byte-stable order independent of declaration order.
    unsat_outputs = []
    for suffix, dependencies in (
        (
            "a",
            (("unsat-left", "=1.0.0"), ("unsat-right", "=1.0.0")),
        ),
        (
            "b",
            (("unsat-right", "=1.0.0"), ("unsat-left", "=1.0.0")),
        ),
    ):
        project = root / f"unsat-{suffix}"
        write_consumer(
            project,
            identity="unsatisfiable",
            dependencies=dependencies,
        )
        completed = install(
            zed,
            registry,
            root / f"unsat-home-{suffix}",
            project,
            environment,
        )
        require_failure(completed)
        assert_no_project_mutation(project)
        normalized = normalized_error(completed.output, root)
        for fragment in (
            f"version conflict for {package_key('unsat-shared')}",
            f"`=1.0.0` via solver-consumer/unsatisfiable@0.1.0 -> "
            f"{package_key('unsat-left')}@1.0.0 -> {package_key('unsat-shared')}",
            f"`=2.0.0` via solver-consumer/unsatisfiable@0.1.0 -> "
            f"{package_key('unsat-right')}@1.0.0 -> {package_key('unsat-shared')}",
        ):
            if fragment not in normalized:
                raise AssertionError(
                    f"unsatisfiable diagnostic omitted {fragment!r}:\n{normalized}"
                )
        unsat_outputs.append(normalized)
    if unsat_outputs[0] != unsat_outputs[1]:
        raise AssertionError(
            "unsatisfiable diagnostic changed with root declaration order:\n"
            f"--- first ---\n{unsat_outputs[0]}\n"
            f"--- second ---\n{unsat_outputs[1]}"
        )

    # 5. A dependency cycle terminates with one selected version per package.
    cycle_project = root / "cycle"
    write_consumer(
        cycle_project,
        identity="cycle",
        dependencies=(("cycle-a", "=1.0.0"),),
    )
    cycle_home = root / "cycle-home"
    cycle = install(zed, registry, cycle_home, cycle_project, environment)
    require_success(cycle)
    cycle_expected = {"cycle-a": "1.0.0", "cycle-b": "1.0.0"}
    assert_lock(cycle_project, cycle_expected)
    assert_symlink_graph(cycle_project, cycle_home, cycle_expected)

    print("\ncomplete constraint solver black-box canary passed", flush=True)
    print(f"overlap summary: {parse_prefetch(overlap_first.output)}", flush=True)
    print(f"backtrack summary: {backtrack_summary}", flush=True)
    print(f"stale-candidate summary: {parse_prefetch(stale.output)}", flush=True)
    print(f"cycle summary: {parse_prefetch(cycle.output)}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001 - CI boundary must print context.
        print(f"error: {error}", file=sys.stderr, flush=True)
        raise
