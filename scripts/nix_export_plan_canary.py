#!/usr/bin/env python3
"""Independent black-box canary for frozen Zed → Nix export planning."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Sequence


def run(
    command: Sequence[str | Path],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    should_fail: bool = False,
) -> str:
    argv = [str(value) for value in command]
    environment = {
        "CI": "true",
        "PATH": os.environ.get("PATH", ""),
        "ZED_PKG_INTERACTIVE": "false",
    }
    if env:
        environment.update(env)
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    print(f"\n$ (cd {cwd} && {' '.join(argv)})", flush=True)
    print(completed.stdout, end="", flush=True)
    if should_fail:
        if completed.returncode == 0:
            raise AssertionError(f"command unexpectedly succeeded: {argv!r}")
    elif completed.returncode != 0:
        raise RuntimeError(
            f"command failed with exit code {completed.returncode}: {argv!r}\n{completed.stdout}"
        )
    return completed.stdout


def git_clean(root: Path) -> None:
    status = run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=root,
    )
    if status.strip():
        raise AssertionError(f"fixture checkout was mutated:\n{status}")


def copy_fixture(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(".git", ".zed", "zed_modules", "node_modules"),
    )


def tree_digest(root: Path) -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            rows.append(
                (path.relative_to(root).as_posix(), f"symlink:{os.readlink(path)}")
            )
        elif path.is_file():
            rows.append(
                (
                    path.relative_to(root).as_posix(),
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
    return tuple(rows)


def append_text(path: Path, value: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(value)


def plan(
    zed: Path,
    project: Path,
    *,
    target: str | None = None,
    env: Mapping[str, str] | None = None,
    should_fail: bool = False,
) -> str:
    command: list[str | Path] = [
        zed,
        "interop",
        "nix",
        "plan",
        "export",
        "--frozen",
        "--json",
    ]
    if target:
        command.extend(["--target", target])
    return run(command, cwd=project, env=env, should_fail=should_fail)


def assert_hex_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise AssertionError(f"{label} is not a SHA-256 hex digest: {value!r}")
    int(value, 16)
    return value


def single_package_canary(fixture: Path, zed: Path, root: Path) -> None:
    first = root / "single-a" / "deep" / "package"
    second = root / "single-b" / "different" / "package"
    copy_fixture(fixture, first)
    copy_fixture(fixture, second)

    nix_intent = """

[publish.nix]
attribute = "node-lib"
systems = ["x86_64-linux", "aarch64-linux"]
outputs = ["out"]

[bin]
node-lib-tool = "bin/node-lib-tool"
"""
    for project in (first, second):
        append_text(project / ".zpkg.toml", nix_intent)
        (project / ".zpkg.lock").write_text("version = 1\n", encoding="utf-8")
        tool = project / "bin" / "node-lib-tool"
        tool.parent.mkdir(parents=True)
        tool.write_text("#!/bin/sh\necho node-lib\n", encoding="utf-8")
        tool.chmod(0o755)

    first_before = tree_digest(first)
    second_before = tree_digest(second)
    forbidden_secret = "planner-secret-must-never-appear"
    unused_home = root / "planner-home-must-not-exist"
    environment = {
        "ZED_PKG_TOKEN": forbidden_secret,
        "ZED_PKG_SUPABASE_KEY": forbidden_secret,
        "ZED_PKG_REGISTRY": "https://person:secret@example.invalid/registry",
        "ZED_PKG_HOME": str(unused_home),
    }
    first_raw = plan(zed, first, env=environment)
    second_raw = plan(zed, second, env=environment)
    if first_raw != second_raw:
        raise AssertionError("identical packages at different absolute paths produced different plans")
    if tree_digest(first) != first_before or tree_digest(second) != second_before:
        raise AssertionError("read-only planning mutated a package copy")
    if unused_home.exists():
        raise AssertionError("planning created the configured global Zed home")
    for forbidden in (forbidden_secret, "person:secret", str(first), str(second), str(root)):
        if forbidden in first_raw:
            raise AssertionError(f"plan leaked forbidden value: {forbidden!r}")

    parsed = json.loads(first_raw)
    if parsed.get("schema") != "zed.nix-export-plan/v1":
        raise AssertionError(f"unexpected plan schema: {parsed!r}")
    package = parsed["package"]
    if (package["org"], package["name"], package["version"]) != (
        "zed-pkg-test",
        "node-lib",
        "1.0.0",
    ):
        raise AssertionError(f"wrong package identity: {package!r}")
    if parsed["package_class"] != "prebuilt-bin":
        raise AssertionError(f"expected prebuilt-bin plan: {parsed!r}")
    if parsed["bins"] != {"node-lib-tool": "bin/node-lib-tool"}:
        raise AssertionError(f"wrong bin inventory: {parsed['bins']!r}")
    if parsed["intent"]["systems"] != ["aarch64-linux", "x86_64-linux"]:
        raise AssertionError(f"systems were not canonicalized: {parsed['intent']!r}")
    assert_hex_sha256(parsed["source"]["artifact"]["sha256"], "artifact")
    assert_hex_sha256(parsed["source"]["manifest_sha256"], "manifest")
    assert_hex_sha256(parsed["source"]["lock_sha256"], "lock")

    commented = root / "single-commented"
    copy_fixture(first, commented)
    original = (commented / ".zpkg.toml").read_text(encoding="utf-8")
    (commented / ".zpkg.toml").write_text(
        "# exact input comment\n" + original,
        encoding="utf-8",
    )
    commented_plan = json.loads(plan(zed, commented))
    if commented_plan["source"]["manifest_sha256"] == parsed["source"]["manifest_sha256"]:
        raise AssertionError("exact manifest-byte change did not alter manifest identity")
    if commented_plan["source"]["artifact"]["sha256"] == parsed["source"]["artifact"]["sha256"]:
        raise AssertionError("manifest-byte change did not alter immutable artifact identity")

    missing_lock = root / "single-missing-lock"
    copy_fixture(first, missing_lock)
    (missing_lock / ".zpkg.lock").unlink()
    failure = plan(zed, missing_lock, should_fail=True)
    if "requires existing lockfile" not in failure:
        raise AssertionError(f"missing-lock failure was not actionable:\n{failure}")

    source_build = root / "single-source-build"
    copy_fixture(first, source_build)
    append_text(source_build / ".zpkg.toml", "\n[build]\ncommand = \"make\"\n")
    failure = plan(zed, source_build, should_fail=True)
    if "does not infer source builds" not in failure:
        raise AssertionError(f"source-build failure was not actionable:\n{failure}")


def polyglot_canary(fixture: Path, zed: Path, root: Path) -> None:
    project = root / "polyglot"
    copy_fixture(fixture, project)
    append_text(
        project / ".zpkg.toml",
        """

[targets.nodejs.nix]
attribute = "polyglot-node"
systems = ["x86_64-linux", "aarch64-linux"]
outputs = ["out"]

[targets.rust.nix]
attribute = "polyglot-rust"
systems = ["x86_64-linux"]
outputs = ["out"]
""",
    )
    (project / ".zpkg.lock").write_text("version = 1\n", encoding="utf-8")
    before = tree_digest(project)

    failure = plan(zed, project, should_fail=True)
    if "requires --target" not in failure:
        raise AssertionError(f"ambiguous polyglot failure was not actionable:\n{failure}")

    node_alias = plan(zed, project, target="node")
    node_exact = plan(zed, project, target="nodejs")
    if node_alias != node_exact:
        raise AssertionError("target synonym and exact target produced different plans")
    node = json.loads(node_exact)
    rust = json.loads(plan(zed, project, target="rust"))
    if node["package"]["name"] != "polyglot-lib-nodejs":
        raise AssertionError(f"Node target was not re-rooted: {node!r}")
    if node["package"].get("target") != "nodejs":
        raise AssertionError(f"Node target provenance missing: {node!r}")
    if rust["package"]["name"] != "polyglot-lib-rust":
        raise AssertionError(f"Rust target was not re-rooted: {rust!r}")
    if node["source"]["artifact"]["sha256"] == rust["source"]["artifact"]["sha256"]:
        raise AssertionError("isolated Node and Rust targets produced the same artifact identity")
    if tree_digest(project) != before:
        raise AssertionError("polyglot planning mutated the fixture copy")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node-fixture", required=True, type=Path)
    parser.add_argument("--polyglot-fixture", required=True, type=Path)
    parser.add_argument("--zed", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    node_fixture = args.node_fixture.resolve()
    polyglot_fixture = args.polyglot_fixture.resolve()
    zed = args.zed.resolve()
    root = args.work_root.resolve()

    if root.exists():
        raise AssertionError(f"work root must be fresh: {root}")
    if not zed.is_file():
        raise AssertionError(f"zed binary not found: {zed}")
    root.mkdir(parents=True)

    git_clean(node_fixture)
    git_clean(polyglot_fixture)
    single_package_canary(node_fixture, zed, root)
    polyglot_canary(polyglot_fixture, zed, root)
    git_clean(node_fixture)
    git_clean(polyglot_fixture)

    print(
        json.dumps(
            {
                "result": "PASS",
                "schema": "zed.nix-export-plan/v1",
                "single_fixture": "zed-pkg-test/node-lib",
                "polyglot_fixture": "zed-pkg-test/polyglot-lib",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
