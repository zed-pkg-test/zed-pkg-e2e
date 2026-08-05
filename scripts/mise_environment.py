#!/usr/bin/env python3
"""Black-box certification for zed-pkg's project-local mise adapter.

The harness intentionally runs the real `zed` executable with an empty PATH.
It proves that import/verification parses committed manager state directly,
never invokes mise, never walks to parent/global configuration, and does not
mutate the consumer project.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable


class HarnessFailure(RuntimeError):
    pass


def sha256_hex(digit: str) -> str:
    return "sha256:" + digit * 64


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_snapshot(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = f"symlink:{os.readlink(path)}"
        elif path.is_file():
            snapshot[relative] = f"file:{file_digest(path)}"
        elif path.is_dir():
            snapshot[relative] = "dir"
    return snapshot


def ensure(condition: bool, message: str) -> None:
    if not condition:
        raise HarnessFailure(message)


def run_zed(
    zed: Path,
    project: Path,
    home: Path,
    args: Iterable[str],
    *,
    expect_success: bool,
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
            "MISE_GLOBAL_CONFIG_FILE": str(home / ".config" / "mise" / "config.toml"),
            # The executable is invoked by absolute path. Any attempt to shell
            # out to mise or another ambient tool must fail.
            "PATH": str(empty_path),
        }
    )
    for name in (
        "ZED_PKG_ENV_CONFIG",
        "ZED_PKG_ENV_LOCK",
        "ZED_PKG_ENV_JSON",
        "ZED_PKG_FROZEN",
        "MISE_CONFIG_FILE",
        "MISE_TOML",
    ):
        env.pop(name, None)

    command = [str(zed), *args]
    completed = subprocess.run(
        command,
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    succeeded = completed.returncode == 0
    if succeeded != expect_success:
        raise HarnessFailure(
            "unexpected command result\n"
            f"command: {command!r}\n"
            f"cwd: {project}\n"
            f"returncode: {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    if contains is not None:
        combined = completed.stdout + "\n" + completed.stderr
        ensure(
            contains in combined,
            f"expected {contains!r} in command output, got:\n{combined}",
        )
    return completed


def parse_json(completed: subprocess.CompletedProcess[str]) -> Any:
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise HarnessFailure(
            f"command did not emit valid JSON: {error}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        ) from error


def baseline_config(*, reordered: bool = False) -> str:
    if reordered:
        return """[tools]
python="3.12"
node="22"

[settings]
lockfile_platforms=["macos-arm64","linux-x64"]
lockfile=true
"""
    return """[settings]
lockfile = true
lockfile_platforms = ["linux-x64", "macos-arm64"]

[tools]
node = "22"
python = "3.12"
"""


def baseline_lock(*, reordered: bool = False, node_url: str = "https://example.invalid/node") -> str:
    node = f"""[[tools.node]]
version = "22.4.0"
backend = "core:node"

[tools.node.platforms.linux-x64]
checksum = "{sha256_hex('a')}"
size = 100
url = "{node_url}/linux-x64"

[tools.node.platforms.macos-arm64]
url = "{node_url}/macos-arm64"
size = 101
checksum = "{sha256_hex('c')}"
"""
    python = f"""[[tools.python]]
version = "3.12.4"
backend = "core:python"

[tools.python.platforms.linux-x64]
checksum = "{sha256_hex('d')}"
size = 199
url = "https://example.invalid/python-linux"

[tools.python.platforms.macos-arm64]
url = "https://example.invalid/python-macos"
size = 200
checksum = "{sha256_hex('b')}"
"""
    return (python + "\n" + node) if reordered else (node + "\n" + python)

def write_locked_project(project: Path, *, reordered: bool = False, node_url: str | None = None) -> None:
    project.mkdir(parents=True, exist_ok=True)
    write(project / "mise.toml", baseline_config(reordered=reordered))
    write(
        project / "mise.lock",
        baseline_lock(
            reordered=reordered,
            node_url=node_url or "https://example.invalid/node",
        ),
    )


def verify_json(zed: Path, project: Path, home: Path) -> dict[str, Any]:
    completed = run_zed(
        zed,
        project,
        home,
        (
            "env",
            "verify",
            "mise",
            "--config",
            "mise.toml",
            "--lock",
            "mise.lock",
            "--frozen",
            "--json",
        ),
        expect_success=True,
    )
    result = parse_json(completed)
    ensure(result.get("manager") == "mise", f"wrong manager: {result!r}")
    ensure(result.get("config") == "mise.toml", f"wrong config: {result!r}")
    ensure(result.get("lock") == "mise.lock", f"wrong lock: {result!r}")
    ensure(result.get("tools") == 2, f"wrong tool count: {result!r}")
    ensure(result.get("verified") is True, f"not verified: {result!r}")
    digest = result.get("environment_plan_sha256")
    ensure(isinstance(digest, str) and len(digest) == 64, f"invalid digest: {result!r}")
    ensure(all(character in "0123456789abcdef" for character in digest), f"non-hex digest: {digest!r}")
    return result


def certify(zed: Path, work_root: Path) -> None:
    ensure(zed.is_file(), f"zed executable does not exist: {zed}")
    ensure(not work_root.exists(), f"work root must be fresh: {work_root}")
    work_root.mkdir(parents=True)

    home = work_root / "home"
    write(home / ".config" / "mise" / "config.toml", '[tools]\nglobal_canary = "latest"\n')

    workspace = work_root / "workspace"
    write(workspace / "mise.toml", '[tools]\nparent_canary = "latest"\n')
    project = workspace / "project"
    write_locked_project(project)

    before = tree_snapshot(project)
    verified = verify_json(zed, project, home)
    after = tree_snapshot(project)
    ensure(before == after, f"verification mutated project state:\nbefore={before!r}\nafter={after!r}")

    imported = parse_json(
        run_zed(
            zed,
            project,
            home,
            (
                "env",
                "import",
                "mise",
                "--config",
                "mise.toml",
                "--lock",
                "mise.lock",
                "--frozen",
                "--json",
            ),
            expect_success=True,
        )
    )
    tools = imported.get("tools", {})
    ensure(set(tools) == {"node", "python"}, f"parent/global config leaked: {tools!r}")
    ensure(tools["node"].get("requirement") == "22", f"wrong node requirement: {tools!r}")
    ensure(tools["node"].get("resolved") == "22.4.0", f"wrong node resolution: {tools!r}")
    ensure(tools["node"].get("backend") == "core:node", f"wrong node backend: {tools!r}")
    ensure(imported.get("activation") == "frozen-install", f"wrong activation: {imported!r}")

    # Equivalent manager state must have the same semantic identity despite
    # TOML key/table ordering and presentation differences.
    reordered = work_root / "reordered"
    write_locked_project(reordered, reordered=True)
    reordered_result = verify_json(zed, reordered, home)
    ensure(
        reordered_result["environment_plan_sha256"] == verified["environment_plan_sha256"],
        "presentation-only TOML changes altered the normalized environment identity",
    )

    # Manager provenance is part of semantic drift identity even when the
    # artifact checksum remains unchanged.
    mirror = work_root / "mirror"
    write_locked_project(mirror, reordered=True, node_url="https://mirror.invalid/node")
    mirror_result = verify_json(zed, mirror, home)
    ensure(
        mirror_result["environment_plan_sha256"] != verified["environment_plan_sha256"],
        "changing locked artifact provenance did not alter the environment identity",
    )

    malformed = work_root / "malformed"
    write_locked_project(malformed)
    malformed_text = (malformed / "mise.lock").read_text(encoding="utf-8").replace(
        sha256_hex("a"), "sha256:not-hex"
    )
    write(malformed / "mise.lock", malformed_text)
    run_zed(
        zed,
        malformed,
        home,
        ("env", "verify", "mise", "--frozen", "--json"),
        expect_success=False,
        contains="invalid checksum",
    )

    no_checksum = work_root / "no-checksum"
    write_locked_project(no_checksum)
    no_checksum_text = "\n".join(
        line
        for line in (no_checksum / "mise.lock").read_text(encoding="utf-8").splitlines()
        if sha256_hex("a") not in line
    ) + "\n"
    write(no_checksum / "mise.lock", no_checksum_text)
    run_zed(
        zed,
        no_checksum,
        home,
        ("env", "verify", "mise", "--frozen", "--json"),
        expect_success=False,
        contains="no cryptographic checksum",
    )

    drift = work_root / "drift"
    write_locked_project(drift)
    write(drift / "mise.toml", baseline_config() + '\ngo = "1.24"\n')
    run_zed(
        zed,
        drift,
        home,
        ("env", "verify", "mise", "--frozen", "--json"),
        expect_success=False,
        contains="lock/config drift",
    )

    ambiguous = work_root / "ambiguous"
    write_locked_project(ambiguous)
    write(ambiguous / ".mise.toml", '[tools]\nruby = "3.4"\n')
    run_zed(
        zed,
        ambiguous,
        home,
        ("env", "verify", "mise", "--frozen", "--json"),
        expect_success=False,
        contains="multiple project-local",
    )

    child = workspace / "child-without-config"
    child.mkdir()
    run_zed(
        zed,
        child,
        home,
        ("env", "import", "mise", "--json"),
        expect_success=False,
        contains="no project-local mise configuration",
    )

    tool_versions = work_root / "tool-versions"
    tool_versions.mkdir()
    write(tool_versions / ".tool-versions", "node 22.4.0\npython 3.12.4\n")
    authoring_plan = parse_json(
        run_zed(
            zed,
            tool_versions,
            home,
            ("env", "import", "mise", "--config", ".tool-versions", "--json"),
            expect_success=True,
        )
    )
    ensure(set(authoring_plan.get("tools", {})) == {"node", "python"}, "bad .tool-versions import")
    run_zed(
        zed,
        tool_versions,
        home,
        (
            "env",
            "verify",
            "mise",
            "--config",
            ".tool-versions",
            "--frozen",
            "--json",
        ),
        expect_success=False,
        contains="requires a project-local lockfile",
    )

    outside = work_root / "outside.toml"
    write(outside, '[tools]\nnode = "22"\n')
    escape = work_root / "symlink-escape"
    escape.mkdir()
    try:
        (escape / "mise.toml").symlink_to(outside)
    except (OSError, NotImplementedError):
        pass
    else:
        run_zed(
            zed,
            escape,
            home,
            ("env", "import", "mise", "--config", "mise.toml", "--json"),
            expect_success=False,
            contains="escapes the project root",
        )

    print(
        json.dumps(
            {
                "certified": True,
                "manager": "mise",
                "environment_plan_sha256": verified["environment_plan_sha256"],
                "project_mutation": False,
                "ambient_mise_required": False,
                "parent_global_leakage": False,
            },
            indent=2,
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
    except HarnessFailure as error:
        print(f"mise environment certification failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
