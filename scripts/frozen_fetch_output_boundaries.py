#!/usr/bin/env python3
"""External canonical-output checks for `zed fetch --frozen`."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence


def digest_tree(root: Path) -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            rows.append(
                (
                    path.relative_to(root).as_posix(),
                    f"symlink:{os.readlink(path)}",
                )
            )
        elif path.is_file():
            rows.append(
                (
                    path.relative_to(root).as_posix(),
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
    return tuple(rows)


def run_failure(command: Sequence[str | Path], *, cwd: Path) -> str:
    argv = [str(item) for item in command]
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env={
            "CI": "true",
            "GIT_TERMINAL_PROMPT": "0",
            "PATH": os.environ.get("PATH", ""),
            "ZED_PKG_INTERACTIVE": "false",
            "ZED_PKG_TOKEN": "",
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    print(f"\n$ {' '.join(argv)}", flush=True)
    print(completed.stdout, end="", flush=True)
    if completed.returncode == 0:
        raise AssertionError(f"command unexpectedly succeeded: {argv!r}")
    return completed.stdout


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    zed = args.zed.resolve()
    root = args.work_root.resolve()
    consumer = root / "consumer"
    registry = root / "registry"
    home = root / "output-boundary-home-must-remain-absent"

    if not zed.is_file():
        raise AssertionError(f"zed binary not found: {zed}")
    if not (consumer / ".zpkg.lock").is_file():
        raise AssertionError(f"external canary consumer lock not found: {consumer}")
    if not registry.is_dir():
        raise AssertionError(f"external canary registry not found: {registry}")

    command_prefix = [
        zed,
        "--registry",
        f"file://{registry}",
        "--home",
        home,
        "fetch",
        "--frozen",
        "--output",
    ]
    before = digest_tree(consumer)

    missing_parent = root / "missing-output-parent"
    missing_output = missing_parent / "bundle"
    failure = run_failure([*command_prefix, missing_output], cwd=consumer)
    if "parent must already exist" not in failure:
        raise AssertionError(f"missing-parent failure was not actionable:\n{failure}")
    if missing_parent.exists() or missing_output.exists():
        raise AssertionError("failed fetch created its missing output parent")

    caller_file = root / "caller-owned-parent-file"
    caller_file.write_text("caller-owned\n", encoding="utf-8")
    failure = run_failure([*command_prefix, caller_file / "bundle"], cwd=consumer)
    if "parent must already exist" not in failure:
        raise AssertionError(f"non-directory-parent failure was not actionable:\n{failure}")
    if caller_file.read_text(encoding="utf-8") != "caller-owned\n":
        raise AssertionError("failed fetch changed a caller-owned parent file")

    redirect = root / "output-parent-redirect"
    os.symlink(consumer, redirect, target_is_directory=True)
    redirected_output = redirect / "generated"
    failure = run_failure([*command_prefix, redirected_output], cwd=consumer)
    if "canonical fetch output" not in failure:
        raise AssertionError(f"symlink-redirection failure was not actionable:\n{failure}")
    if (consumer / "generated").exists():
        raise AssertionError("symlinked output parent redirected state into the project")

    if digest_tree(consumer) != before:
        raise AssertionError("output-boundary failures mutated the consumer project")
    if home.exists():
        raise AssertionError("output-boundary failures wrote the configured Zed home")

    escaped = sorted(
        path.name for path in root.iterdir() if path.name.startswith(".zed-fetch-")
    )
    if escaped:
        raise AssertionError(f"output-boundary failures leaked temporary state: {escaped}")

    print(
        "PASS: missing, non-directory, and symlinked output parents fail without mutation"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
