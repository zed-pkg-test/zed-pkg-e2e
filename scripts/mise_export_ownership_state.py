#!/usr/bin/env python3
"""Black-box regressions for Zed-owned mise export state integrity."""

from __future__ import annotations

import argparse
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


def write_plan(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": 2,
                "tools": {"node": [{"requirement": "22.4.0"}]},
                "env": {"APP_ENV": "test"},
                "vars": {},
                "tasks": {},
                "platforms": ["linux-x64", "macos-arm64", "windows-x64"],
                "activation": "none",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


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
    for key in tuple(env):
        if key.startswith("ZED_PKG_ENV_") or key in {"MISE_CONFIG_FILE", "MISE_TOML"}:
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


def export_args(plan: str) -> tuple[str, ...]:
    return (
        "env",
        "export",
        "mise",
        "--plan",
        plan,
        "--output",
        ".mise.toml",
        "--write",
        "--json",
    )


def certify(zed: Path, root: Path) -> None:
    ensure(zed.is_file(), f"missing zed executable: {zed}")
    ensure(not root.exists(), f"work root must be fresh: {root}")
    root.mkdir(parents=True)
    home = root / "home"
    home.mkdir()
    project = root / "project"
    project.mkdir()

    write_plan(project / "plan-one.json")
    write_plan(project / "plan-two.json")

    first = run(zed, project, home, export_args("plan-one.json"), success=True)
    ensure(json.loads(first.stdout).get("action") == "written", "initial export was not written")

    output_path = project / ".mise.toml"
    state_path = project / ".zed/mise-export-state.json"
    output_before = output_path.read_bytes()
    state_before = state_path.read_bytes()

    run(
        zed,
        project,
        home,
        export_args("plan-two.json"),
        success=False,
        contains="change ownership",
    )
    ensure(output_path.read_bytes() == output_before, "ownership-transfer failure changed output")
    ensure(state_path.read_bytes() == state_before, "ownership-transfer failure changed state")

    state = json.loads(state_before)
    record = state["outputs"][".mise.toml"]
    record["output_sha256"] = "0" * 64
    corrupted = (json.dumps(state, indent=2, sort_keys=True) + "\n").encode()
    state_path.write_bytes(corrupted)

    run(
        zed,
        project,
        home,
        export_args("plan-one.json"),
        success=False,
        contains="export state records sha256",
    )
    ensure(output_path.read_bytes() == output_before, "state-corruption failure changed output")
    ensure(state_path.read_bytes() == corrupted, "state-corruption failure repaired state silently")

    print(
        json.dumps(
            {
                "schema": "zed-pkg.mise-export-ownership-state.v1",
                "certified": True,
                "ownership_transfer_rejected": True,
                "stale_digest_rejected": True,
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
    except (HarnessFailure, json.JSONDecodeError, KeyError) as error:
        print(f"mise export ownership-state certification failed: {error}", file=sys.stderr)
        return 1
    finally:
        if args.work_root.exists() and os.environ.get("KEEP_MISE_EXPORT_ROOT") != "1":
            shutil.rmtree(args.work_root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
