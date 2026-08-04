#!/usr/bin/env python3
"""Certify mise's normalized adjacent lockfile naming through the real zed CLI."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def fail(message: str) -> None:
    raise RuntimeError(message)


def run(
    zed: Path,
    project: Path,
    home: Path,
    *args: str,
    expect_success: bool,
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
    completed = subprocess.run(
        [str(zed), *args],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if (completed.returncode == 0) != expect_success:
        fail(
            "unexpected command result\n"
            f"args={args!r}\n"
            f"returncode={completed.returncode}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return completed


def write_fixture(project: Path, lock_name: str) -> None:
    project.mkdir(parents=True)
    (project / ".mise.toml").write_text('[tools]\nnode = "22"\n', encoding="utf-8")
    (project / lock_name).write_text(
        "[[tools.node]]\n"
        'version = "22.4.0"\n'
        'backend = "core:node"\n'
        "[tools.node.platforms.linux-x64]\n"
        f'checksum = "sha256:{"a" * 64}"\n',
        encoding="utf-8",
    )


def certify(zed: Path, work_root: Path) -> None:
    if not zed.is_file():
        fail(f"zed executable does not exist: {zed}")
    if work_root.exists():
        fail(f"work root must be fresh: {work_root}")
    work_root.mkdir(parents=True)
    home = work_root / "home"
    home.mkdir()

    normalized = work_root / "normalized"
    write_fixture(normalized, "mise.lock")
    completed = run(
        zed,
        normalized,
        home,
        "env",
        "verify",
        "mise",
        "--frozen",
        "--json",
        expect_success=True,
    )
    result = json.loads(completed.stdout)
    if result.get("config") != ".mise.toml":
        fail(f"unexpected implicit config: {result!r}")
    if result.get("lock") != "mise.lock":
        fail(f"unexpected implicit lock: {result!r}")
    if (normalized / ".mise.lock").exists():
        fail("verification invented .mise.lock")

    explicit = run(
        zed,
        normalized,
        home,
        "env",
        "verify",
        "mise",
        "--config",
        ".mise.toml",
        "--frozen",
        "--json",
        expect_success=True,
    )
    explicit_result = json.loads(explicit.stdout)
    if explicit_result.get("lock") != "mise.lock":
        fail(f"explicit .mise.toml did not normalize to mise.lock: {explicit_result!r}")

    legacy = work_root / "legacy-dot-lock"
    write_fixture(legacy, ".mise.lock")
    rejected = run(
        zed,
        legacy,
        home,
        "env",
        "verify",
        "mise",
        "--config",
        ".mise.toml",
        "--frozen",
        "--json",
        expect_success=False,
    )
    combined = rejected.stdout + "\n" + rejected.stderr
    if "requires a project-local lockfile" not in combined:
        fail(f"legacy .mise.lock was not rejected clearly:\n{combined}")

    print(
        json.dumps(
            {
                "certified": True,
                "config": ".mise.toml",
                "lock": "mise.lock",
                "legacy_dot_lock_rejected": True,
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
    except (RuntimeError, json.JSONDecodeError) as error:
        print(f"mise lock naming certification failed: {error}", file=sys.stderr)
        return 1
    finally:
        if args.work_root.exists() and os.environ.get("KEEP_MISE_LOCK_NAMING_ROOT") != "1":
            shutil.rmtree(args.work_root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
