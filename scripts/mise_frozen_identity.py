#!/usr/bin/env python3
"""Black-box negative and positive tests for frozen mise identity parity."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable


class HarnessFailure(RuntimeError):
    pass


def ensure(condition: bool, message: str) -> None:
    if not condition:
        raise HarnessFailure(message)


def checksum(digit: str) -> str:
    return "sha256:" + digit * 64


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): digest(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def run(
    zed: Path,
    project: Path,
    home: Path,
    args: Iterable[str],
    *,
    success: bool,
    contains: str | None = None,
) -> subprocess.CompletedProcess[str]:
    empty_path = home / "empty-path"
    empty_path.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "ZED_PKG_HOME": str(home / ".zed-pkg"),
            "PATH": str(empty_path),
        }
    )
    for key in (
        "ZED_PKG_ENV_CONFIG",
        "ZED_PKG_ENV_LOCK",
        "ZED_PKG_ENV_JSON",
        "ZED_PKG_FROZEN",
        "MISE_CONFIG_FILE",
        "MISE_TOML",
    ):
        env.pop(key, None)

    completed = subprocess.run(
        [str(zed), *args],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if (completed.returncode == 0) != success:
        raise HarnessFailure(
            f"unexpected status {completed.returncode} for {list(args)!r}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    if contains is not None:
        combined = completed.stdout + "\n" + completed.stderr
        ensure(contains in combined, f"missing {contains!r} in:\n{combined}")
    return completed


def config(requirement: str = "^1.7", *, os_constraint: str | None = None) -> str:
    tool = f'"aqua:jqlang/jq" = "{requirement}"'
    if os_constraint is not None:
        tool = (
            '"aqua:jqlang/jq" = { version = '
            f'"{requirement}", os = "{os_constraint}" }}'
        )
    return (
        '[settings]\n'
        'lockfile_platforms = ["linux-x64", "macos-arm64"]\n\n'
        '[tools]\n'
        f'{tool}\n'
    )


def lock(backend: str, version: str, platforms: tuple[str, ...]) -> str:
    output = (
        '[[tools."aqua:jqlang/jq"]]\n'
        f'version = "{version}"\n'
        f'backend = "{backend}"\n\n'
    )
    for index, platform in enumerate(platforms):
        output += (
            f'[tools."aqua:jqlang/jq".platforms.{platform}]\n'
            f'checksum = "{checksum(chr(ord("a") + index))}"\n'
            f'url = "https://example.invalid/jq-{platform}"\n\n'
        )
    return output


def write_case(
    root: Path,
    *,
    requirement: str = "^1.7",
    backend: str = "aqua:jqlang/jq",
    version: str = "1.7.1",
    platforms: tuple[str, ...] = ("linux-x64", "macos-arm64"),
    os_constraint: str | None = None,
) -> None:
    root.mkdir(parents=True)
    write(root / "mise.toml", config(requirement, os_constraint=os_constraint))
    write(root / "mise.lock", lock(backend, version, platforms))


def verify(zed: Path, project: Path, home: Path, *, success: bool, contains: str | None = None):
    before = snapshot(project)
    completed = run(
        zed,
        project,
        home,
        ("env", "verify", "mise", "--frozen", "--json"),
        success=success,
        contains=contains,
    )
    ensure(snapshot(project) == before, f"verification mutated {project}")
    return completed


def certify(zed: Path, root: Path) -> None:
    ensure(zed.is_file(), f"missing zed executable: {zed}")
    ensure(not root.exists(), f"work root must be fresh: {root}")
    root.mkdir(parents=True)
    home = root / "home"
    home.mkdir()

    valid = root / "valid"
    write_case(valid)
    completed = verify(zed, valid, home, success=True)
    result = json.loads(completed.stdout)
    ensure(result.get("verified") is True, f"valid fixture not verified: {result!r}")

    missing_platform = root / "missing-platform"
    write_case(missing_platform, platforms=("linux-x64",))
    verify(
        zed,
        missing_platform,
        home,
        success=False,
        contains="missing requested locked platform",
    )

    constrained = root / "os-constrained"
    write_case(
        constrained,
        platforms=("linux-x64",),
        os_constraint="linux",
    )
    verify(zed, constrained, home, success=True)

    backend_drift = root / "backend-drift"
    write_case(backend_drift, backend="core:node")
    verify(zed, backend_drift, home, success=False, contains="mise backend drift")

    version_drift = root / "version-drift"
    write_case(version_drift, version="1.6.0")
    verify(zed, version_drift, home, success=False, contains="mise version drift")

    prefix_ok = root / "prefix-ok"
    write_case(prefix_ok, requirement="22", version="22.4.0")
    verify(zed, prefix_ok, home, success=True)

    prefix_boundary = root / "prefix-boundary"
    write_case(prefix_boundary, requirement="22", version="220.0.0")
    verify(zed, prefix_boundary, home, success=False, contains="mise version drift")

    unsupported_range = root / "unsupported-range"
    write_case(unsupported_range, requirement="^1.7 || banana", version="1.7.1")
    verify(
        zed,
        unsupported_range,
        home,
        success=False,
        contains="unsupported mise SemVer requirement",
    )

    print(
        json.dumps(
            {
                "schema": "zed-pkg.mise-frozen-identity.v1",
                "certified": True,
                "requested_platforms": True,
                "tool_os_constraints": True,
                "backend_equality": True,
                "requirement_satisfaction": True,
                "read_only": True,
                "ambient_mise_required": False,
            },
            sort_keys=True,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        certify(args.zed.resolve(), args.work_root.resolve())
    except (HarnessFailure, json.JSONDecodeError) as error:
        print(f"mise frozen identity certification failed: {error}", file=sys.stderr)
        return 1
    finally:
        if args.work_root.exists() and os.environ.get("KEEP_MISE_IDENTITY_ROOT") != "1":
            shutil.rmtree(args.work_root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
