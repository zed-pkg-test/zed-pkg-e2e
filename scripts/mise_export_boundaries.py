#!/usr/bin/env python3
"""Black-box certification for deterministic, conflict-safe mise export."""

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


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def snapshot_without_operation_rendezvous(root: Path) -> dict[str, str]:
    """Snapshot semantic project state while validating the lock separately."""
    relative_lock = Path(".zed/operation.lock")
    return {
        relative: digest
        for relative, digest in snapshot(root).items()
        if Path(relative) != relative_lock
    }


def assert_regular_operation_rendezvous(root: Path) -> None:
    operation_lock = root / ".zed/operation.lock"
    ensure(operation_lock.is_file(), "mutation did not retain the operation rendezvous")
    ensure(not operation_lock.is_symlink(), "operation rendezvous is a symlink")
    unexpected = [
        path.relative_to(root).as_posix()
        for path in sorted((root / ".zed").rglob("*"))
        if path != operation_lock
    ]
    ensure(
        not unexpected,
        f"failed mutation left native adapter state: {unexpected!r}",
    )

def base_plan() -> dict[str, object]:
    return {
        "schema": 2,
        "tools": {
            "node": [
                {"requirement": "22.4.0"},
                {"requirement": "20.15.1"},
            ]
        },
        "env": {"APP_ENV": "test", "RETRIES": 3},
        "vars": {"release": {"channel": "stable"}},
        "tasks": {
            "prepare": {"run": ["echo prepare"]},
            "setup": {
                "description": "Restore dependencies",
                "aliases": ["bootstrap"],
                "depends": ["prepare"],
                "run": ["zed install --frozen", "cargo check"],
            },
        },
        "platforms": ["linux-x64", "macos-arm64"],
        "activation": "none",
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
        "ZED_PKG_ENV_PLAN",
        "ZED_PKG_ENV_OUTPUT",
        "ZED_PKG_ENV_JSON",
        "ZED_PKG_ENV_CHECK",
        "ZED_PKG_ENV_WRITE",
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


def export_args(*extra: str, output: str = ".mise.toml") -> tuple[str, ...]:
    return (
        "env",
        "export",
        "mise",
        "--plan",
        "zed-env.json",
        "--output",
        output,
        *extra,
    )


def certify(zed: Path, root: Path) -> None:
    ensure(zed.is_file(), f"missing zed executable: {zed}")
    ensure(not root.exists(), f"work root must be fresh: {root}")
    root.mkdir(parents=True)
    home = root / "home"
    home.mkdir()
    assertions: list[str] = []

    project = root / "owned"
    project.mkdir()
    write_json(project / "zed-env.json", base_plan())
    initial = snapshot(project)

    printed = run(zed, project, home, export_args(), success=True)
    ensure("[tools]" in printed.stdout, "print mode did not emit mise TOML")
    ensure(snapshot(project) == initial, "print mode mutated project")
    assertions.append("print-read-only")

    written = run(
        zed,
        project,
        home,
        export_args("--write", "--json"),
        success=True,
    )
    written_json = json.loads(written.stdout)
    ensure(written_json.get("action") == "written", f"unexpected write: {written_json}")
    ensure((project / ".mise.toml").is_file(), "write mode omitted output")
    ensure(
        (project / ".zed/mise-export-state.json").is_file(),
        "write mode omitted ownership state",
    )
    assertions.append("write-owned")

    checked = run(
        zed,
        project,
        home,
        export_args("--check", "--json"),
        success=True,
    )
    ensure(json.loads(checked.stdout).get("action") == "verified", "check not verified")
    assertions.append("check-verified")

    unchanged = run(
        zed,
        project,
        home,
        export_args("--write", "--json"),
        success=True,
    )
    ensure(
        json.loads(unchanged.stdout).get("action") == "unchanged",
        "idempotent write was not unchanged",
    )
    assertions.append("write-idempotent")

    original_output = (project / ".mise.toml").read_bytes()
    (project / ".mise.toml").write_text("# hand edit\n", encoding="utf-8")
    run(zed, project, home, export_args("--write"), success=False, contains="edited")
    ensure(
        (project / ".mise.toml").read_text(encoding="utf-8") == "# hand edit\n",
        "edited output overwritten",
    )
    (project / ".mise.toml").write_bytes(original_output)
    assertions.append("edited-output-protected")

    unowned = root / "unowned"
    unowned.mkdir()
    write_json(unowned / "zed-env.json", base_plan())
    (unowned / ".mise.toml").write_text('[tools]\nnode = "18"\n', encoding="utf-8")
    before = snapshot_without_operation_rendezvous(unowned)
    run(zed, unowned, home, export_args("--write"), success=False, contains="hand-authored")
    assert_regular_operation_rendezvous(unowned)
    ensure(
        snapshot_without_operation_rendezvous(unowned) == before,
        "unowned output failure mutated semantic project state",
    )
    assertions.append("unowned-output-protected")
    assertions.append("operation-rendezvous-nonsemantic")

    nested = root / "nested-output"
    nested.mkdir()
    write_json(nested / "zed-env.json", base_plan())
    run(
        zed,
        nested,
        home,
        export_args("--write", output="generated/config/.mise.toml"),
        success=True,
    )
    ensure((nested / "generated/config/.mise.toml").is_file(), "safe missing parents not created")
    assertions.append("safe-parent-creation")

    boundaries = root / "boundaries"
    boundaries.mkdir()
    write_json(boundaries / "zed-env.json", base_plan())
    original_plan = (boundaries / "zed-env.json").read_bytes()
    for output, diagnostic in (
        ("zed-env.json", "cannot overwrite"),
        ("ZED-ENV.JSON", "cannot overwrite"),
        (".zed/mise-export-state.json", "reserved export state"),
        (".ZED/MISE-EXPORT-STATE.JSON", "reserved export state"),
        (".zpkg-staging/mise.toml", "reserved transaction staging"),
        (".ZPKG-STAGING/mise.toml", "reserved transaction staging"),
    ):
        run(
            zed,
            boundaries,
            home,
            export_args("--write", output=output),
            success=False,
            contains=diagnostic,
        )
        ensure(
            (boundaries / "zed-env.json").read_bytes() == original_plan,
            f"boundary {output} mutated plan",
        )
    assertions.append("portable-reserved-paths")

    nested_secret = root / "nested-secret"
    nested_secret.mkdir()
    plan = base_plan()
    plan["vars"] = {"release": {"api_token": "plaintext"}}
    write_json(nested_secret / "zed-env.json", plan)
    run(
        zed,
        nested_secret,
        home,
        export_args(),
        success=False,
        contains="vars.release.api_token",
    )
    ensure(not (nested_secret / ".mise.toml").exists(), "secret rejection wrote output")
    assertions.append("nested-secret-rejected")

    tool_secret = root / "tool-secret"
    tool_secret.mkdir()
    plan = base_plan()
    plan["tools"] = {
        "node": [
            {
                "requirement": "22.4.0",
                "options": {"config": {"password": "plaintext"}},
            }
        ]
    }
    write_json(tool_secret / "zed-env.json", plan)
    run(
        zed,
        tool_secret,
        home,
        export_args(),
        success=False,
        contains="tools.node.versions[0].options.config.password",
    )
    assertions.append("nested-tool-secret-rejected")

    symlink_case = root / "symlink-state"
    symlink_case.mkdir()
    write_json(symlink_case / "zed-env.json", base_plan())
    outside = root / "outside-state"
    outside.mkdir()
    try:
        os.symlink(outside, symlink_case / ".zed", target_is_directory=True)
    except (OSError, NotImplementedError):
        assertions.append("symlink-state-skipped")
    else:
        run(
            zed,
            symlink_case,
            home,
            export_args("--write"),
            success=False,
            contains="mise export state crosses a symlink",
        )
        ensure(not (symlink_case / ".mise.toml").exists(), "symlink failure wrote output")
        ensure(not (outside / "mise-export-state.json").exists(), "state escaped project")
        assertions.append("symlink-state-rejected")

    print(
        json.dumps(
            {
                "schema": "zed-pkg.mise-export-boundaries.v1",
                "certified": True,
                "assertions": assertions,
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
        print(f"mise export boundary certification failed: {error}", file=sys.stderr)
        return 1
    finally:
        if args.work_root.exists() and os.environ.get("KEEP_MISE_EXPORT_ROOT") != "1":
            shutil.rmtree(args.work_root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
