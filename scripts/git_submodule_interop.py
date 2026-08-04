#!/usr/bin/env python3
"""Black-box certification for mixed Git/Zed submodule interoperability."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Sequence


GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "Zed Interop Canary",
    "GIT_AUTHOR_EMAIL": "zed-interop-canary@example.invalid",
    "GIT_COMMITTER_NAME": "Zed Interop Canary",
    "GIT_COMMITTER_EMAIL": "zed-interop-canary@example.invalid",
    "GIT_TERMINAL_PROMPT": "0",
}


class ContractError(RuntimeError):
    """Raised when an observable product contract is violated."""


def run(
    argv: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    unset: Sequence[str] = (),
    expect: int = 0,
) -> subprocess.CompletedProcess[str]:
    command = [os.fspath(value) for value in argv]
    merged = os.environ.copy()
    merged.update(GIT_IDENTITY)
    for key in unset:
        merged.pop(key, None)
    if env:
        merged.update(env)
    result = subprocess.run(
        command,
        cwd=cwd,
        env=merged,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != expect:
        raise ContractError(
            f"command returned {result.returncode}, expected {expect}: "
            f"{' '.join(command)}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def git(project: Path, *args: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
    return run(["git", "-C", project, *args], expect=expect)


def init_repo(project: Path) -> None:
    project.mkdir(parents=True)
    git(project, "init")


def commit_all(project: Path, message: str) -> None:
    git(project, "add", ".")
    git(project, "commit", "-m", message)


def write_package(project: Path, org: str, name: str) -> None:
    (project / ".zpkg.toml").write_text(
        f"""[package]
org = "{org}"
name = "{name}"
version = "1.2.3"

[package.repository]
vcs = "git"
url = "https://example.invalid/{org}/{name}.git"
""",
        encoding="utf-8",
    )


def add_submodule(root: Path, source: Path, destination: str) -> None:
    git(
        root,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        os.fspath(source),
        destination,
    )


def zed_env(home: Path, *, git_submodules: str | None = None) -> dict[str, str]:
    environment = {
        "HOME": os.fspath(home),
        "USERPROFILE": os.fspath(home),
        "XDG_CONFIG_HOME": os.fspath(home / ".config"),
        "ZED_PKG_HOME": os.fspath(home / ".zed-pkg"),
        "ZED_PKG_REGISTRY": "file:///unused",
        "GIT_TERMINAL_PROMPT": "0",
    }
    if git_submodules is not None:
        environment["ZED_PKG_GIT_SUBMODULES"] = git_submodules
    return environment


def zed(
    executable: Path,
    project: Path,
    home: Path,
    *args: str,
    expect: int = 0,
    git_submodules: str | None = None,
) -> subprocess.CompletedProcess[str]:
    home.mkdir(parents=True, exist_ok=True)
    return run(
        [executable, *args],
        cwd=project,
        env=zed_env(home, git_submodules=git_submodules),
        unset=(
            "ZED_PKG_GIT_SUBMODULES",
            "ZED_PKG_TOKEN",
            "ZED_TOKEN",
            "GH_TOKEN",
            "GITHUB_TOKEN",
        ),
        expect=expect,
    )


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def require_contains(text: str, needle: str, context: str) -> None:
    require(needle in text, f"{context} did not contain {needle!r}:\n{text}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def certify_mixed_takeover(zed_bin: Path, root: Path) -> dict[str, str]:
    zed_child = root / "zed-child"
    legacy_child = root / "legacy-child"
    superproject = root / "superproject"
    fresh = root / "fresh-clone"

    init_repo(zed_child)
    write_package(zed_child, "acme", "client")
    (zed_child / "lib.txt").write_text("zed package\n", encoding="utf-8")
    commit_all(zed_child, "zed child")

    init_repo(legacy_child)
    (legacy_child / "README.md").write_text(
        "ordinary Git submodule\n", encoding="utf-8"
    )
    commit_all(legacy_child, "legacy child")

    init_repo(superproject)
    git(superproject, "config", "protocol.file.allow", "always")
    write_package(superproject, "acme", "root")
    add_submodule(superproject, zed_child, "vendor/client")
    add_submodule(superproject, legacy_child, "vendor/legacy")
    commit_all(superproject, "root with mixed submodules")

    takeover = zed(
        zed_bin,
        superproject,
        root / "takeover-home",
        "overtake",
        "--git-submodules",
    )
    require_contains(
        takeover.stdout,
        "overtook 1 Git submodule package(s)",
        "takeover stdout",
    )
    require_contains(
        takeover.stdout,
        "left 1 non-Zed submodule(s) under Git authority",
        "takeover stdout",
    )
    require_contains(
        takeover.stderr,
        "leaving non-Zed submodule",
        "takeover stderr",
    )
    require_contains(takeover.stderr, "vendor/legacy", "takeover stderr")

    manifest = superproject / ".zpkg.toml"
    lock = superproject / ".zpkg.lock"
    manifest_text = manifest.read_text(encoding="utf-8")
    lock_text = lock.read_text(encoding="utf-8")
    require_contains(manifest_text, "acme/client", "root manifest")
    require_contains(manifest_text, "vendor/client", "root manifest")
    require("vendor/legacy" not in manifest_text, "legacy path leaked into manifest")
    require_contains(lock_text, "[[git-submodule]]", "root lock")
    require_contains(lock_text, 'package = "acme/client"', "root lock")
    require_contains(lock_text, 'path = "vendor/client"', "root lock")
    require("vendor/legacy" not in lock_text, "legacy path leaked into lock")

    git(superproject, "add", ".zpkg.toml", ".zpkg.lock")
    git(superproject, "commit", "-m", "adopt Zed submodule")

    run(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "clone",
            "--no-recurse-submodules",
            superproject,
            fresh,
        ]
    )
    git(fresh, "config", "protocol.file.allow", "always")
    require(
        not (fresh / "vendor/client/lib.txt").exists(),
        "Zed submodule unexpectedly initialized by clone",
    )
    require(
        not (fresh / "vendor/legacy/README.md").exists(),
        "legacy submodule unexpectedly initialized by clone",
    )

    manifest_before = (fresh / ".zpkg.toml").read_bytes()
    lock_before = (fresh / ".zpkg.lock").read_bytes()
    replay = zed(
        zed_bin,
        fresh,
        root / "fresh-home",
        "install",
        "--frozen",
        git_submodules="yes",
    )
    require_contains(
        replay.stdout,
        "synchronized 2 Git submodule(s)",
        "frozen replay stdout",
    )
    require(
        (fresh / ".zpkg.toml").read_bytes() == manifest_before,
        "frozen replay rewrote the manifest",
    )
    require(
        (fresh / ".zpkg.lock").read_bytes() == lock_before,
        "frozen replay rewrote the lock",
    )
    require(
        (fresh / "vendor/client/lib.txt").read_text(encoding="utf-8")
        == "zed package\n",
        "Zed submodule source was not restored",
    )
    require(
        (fresh / "vendor/legacy/README.md").read_text(encoding="utf-8")
        == "ordinary Git submodule\n",
        "legacy submodule source was not restored",
    )
    require(
        (fresh / "zed_modules/acme/client/lib.txt").read_text(encoding="utf-8")
        == "zed package\n",
        "adopted package was not materialized",
    )

    return {
        "manifest_sha256": hashlib.sha256(manifest_before).hexdigest(),
        "lock_sha256": hashlib.sha256(lock_before).hexdigest(),
        "superproject_commit": git(superproject, "rev-parse", "HEAD").stdout.strip(),
    }


def certify_git_only_boundary(zed_bin: Path, root: Path) -> None:
    legacy_child = root / "legacy-child"
    superproject = root / "superproject"

    init_repo(legacy_child)
    (legacy_child / "README.md").write_text(
        "ordinary Git submodule\n", encoding="utf-8"
    )
    commit_all(legacy_child, "legacy child")

    init_repo(superproject)
    git(superproject, "config", "protocol.file.allow", "always")
    write_package(superproject, "acme", "root")
    add_submodule(superproject, legacy_child, "vendor/legacy")
    commit_all(superproject, "root with Git-only submodule")
    git(
        superproject,
        "submodule",
        "deinit",
        "--force",
        "--",
        "vendor/legacy",
    )
    require(
        not (superproject / "vendor/legacy/README.md").exists(),
        "deinit did not remove the Git-only checkout",
    )

    manifest = superproject / ".zpkg.toml"
    manifest_before = manifest.read_bytes()
    failure = zed(
        zed_bin,
        superproject,
        root / "home",
        "overtake",
        "--git-submodules",
        expect=1,
    )
    require_contains(
        failure.stderr,
        "no overtake-compatible Zed submodules",
        "Git-only takeover stderr",
    )
    require_contains(failure.stderr, "vendor/legacy", "Git-only takeover stderr")
    require(
        manifest.read_bytes() == manifest_before,
        "Git-only takeover changed the root manifest",
    )
    for forbidden in [".zpkg.lock", "zed_modules", ".zpkg-staging"]:
        require(
            not (superproject / forbidden).exists(),
            f"Git-only takeover published forbidden state: {forbidden}",
        )
    require(
        (superproject / "vendor/legacy/README.md").read_text(encoding="utf-8")
        == "ordinary Git submodule\n",
        "cooperative synchronization did not restore the Git-only submodule",
    )


def certify_invalid_manifest_boundary(zed_bin: Path, root: Path) -> None:
    invalid_child = root / "invalid-child"
    superproject = root / "superproject"

    init_repo(invalid_child)
    (invalid_child / ".zpkg.toml").write_text(
        "[package]\nname = [this is not valid TOML\n", encoding="utf-8"
    )
    commit_all(invalid_child, "invalid Zed package")

    init_repo(superproject)
    git(superproject, "config", "protocol.file.allow", "always")
    write_package(superproject, "acme", "root")
    add_submodule(superproject, invalid_child, "vendor/invalid")
    commit_all(superproject, "root with invalid Zed submodule")

    manifest = superproject / ".zpkg.toml"
    manifest_before = manifest.read_bytes()
    failure = zed(
        zed_bin,
        superproject,
        root / "home",
        "overtake",
        "--git-submodules",
        expect=1,
    )
    require_contains(
        failure.stderr,
        "contains an invalid .zpkg.toml",
        "invalid-manifest takeover stderr",
    )
    require(
        manifest.read_bytes() == manifest_before,
        "invalid package manifest changed the root manifest",
    )
    require(
        not (superproject / ".zpkg.lock").exists(),
        "invalid package manifest produced a lockfile",
    )
    require(
        not (superproject / "zed_modules").exists(),
        "invalid package manifest materialized packages",
    )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    zed_bin = args.zed.resolve()
    work_root = args.work_root.resolve()

    require(zed_bin.is_file(), f"zed executable does not exist: {zed_bin}")
    require(not work_root.exists(), f"work root must not exist: {work_root}")
    work_root.mkdir(parents=True)

    try:
        mixed = certify_mixed_takeover(zed_bin, work_root / "mixed")
        certify_git_only_boundary(zed_bin, work_root / "git-only")
        certify_invalid_manifest_boundary(zed_bin, work_root / "invalid-manifest")
        result = {
            "schema": "zed.git-submodule-interop-canary/v1",
            "zed_sha256": sha256(zed_bin),
            "mixed": mixed,
            "git_only_boundary": "passed",
            "invalid_manifest_boundary": "passed",
        }
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception:
        print(f"diagnostic work root retained at {work_root}", file=sys.stderr)
        raise


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
