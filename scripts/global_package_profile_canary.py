#!/usr/bin/env python3
"""Black-box certification for zed global executable package profiles.

The harness builds no registry service and uses no credential. It creates two
small immutable package artifacts in a disposable file:// registry, invokes only
the compiled zed CLI, and checks PATH ownership, frozen restoration, collision
rollback, and tamper-preserving uninstall behavior.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path
from typing import Any

COMMAND = "zed-global-canary"
VERSION = "1.0.0"
VCS_COMMIT = "0123456789abcdef0123456789abcdef01234567"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest(name: str) -> bytes:
    return f'''[package]
org = "canary"
name = "{name}"
version = "{VERSION}"
description = "Disposable global package canary {name}"
license = "MIT"

[package.repository]
vcs = "git"
url = "https://example.invalid/canary/{name}"

[bin]
{COMMAND} = "bin/{COMMAND}"

[install]
adapter = "none"
'''.encode()


def command_bytes(name: str) -> bytes:
    return (
        "#!/bin/sh\n"
        "set -eu\n"
        f"printf '%s\\n' 'canary/{name}:{VERSION}'\n"
    ).encode()


def add_tar_member(archive: tarfile.TarFile, name: str, data: bytes, mode: int) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    archive.addfile(info, io.BytesIO(data))


def artifact(name: str) -> bytes:
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT) as archive:
            add_tar_member(archive, "pkg/.zpkg.toml", manifest(name), 0o644)
            add_tar_member(archive, f"pkg/bin/{COMMAND}", command_bytes(name), 0o755)
            add_tar_member(archive, "pkg/LICENSE", b"MIT\n", 0o644)
    return output.getvalue()


def publish_fixture(registry: Path, name: str) -> dict[str, Any]:
    payload = artifact(name)
    digest = sha256(payload)
    artifact_path = registry / "artifacts" / f"{digest}.tar.gz"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(payload)

    package_dir = registry / "packages" / "canary" / name
    version_dir = package_dir / "versions"
    version_dir.mkdir(parents=True, exist_ok=True)
    package_metadata = {
        "org": "canary",
        "name": name,
        "description": f"Disposable global package canary {name}",
        "vcs": "git",
        "repo_url": f"https://example.invalid/canary/{name}",
        "latest": VERSION,
        "tags": ["global-package-canary"],
        "versions": [VERSION],
    }
    version_metadata = {
        "org": "canary",
        "name": name,
        "version": VERSION,
        "sha256": digest,
        "size": len(payload),
        "format": "tar.gz",
        "vcs_tag": f"v{VERSION}",
        "vcs_commit": VCS_COMMIT,
        "download_url": artifact_path.resolve().as_uri(),
        "published_at": "2026-08-04T00:00:00Z",
        "yanked": False,
    }
    (package_dir / "package.json").write_text(
        json.dumps(package_metadata, sort_keys=True, indent=2) + "\n"
    )
    (version_dir / f"{VERSION}.json").write_text(
        json.dumps(version_metadata, sort_keys=True, indent=2) + "\n"
    )
    return {
        "package": f"canary/{name}@{VERSION}",
        "sha256": digest,
        "size": len(payload),
    }


def executable_path(bin_dir: Path) -> Path:
    return bin_dir / (f"{COMMAND}.exe" if os.name == "nt" else COMMAND)


def profile_path(home: Path, name: str) -> Path:
    return home / "global" / "profiles" / "canary" / name


def run(
    zed: Path,
    home: Path,
    bin_dir: Path,
    registry: Path,
    args: list[str],
    evidence: list[dict[str, Any]],
    *,
    expected: int = 0,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "ZED_PKG_HOME": str(home),
            "ZED_PKG_GLOBAL_BIN_DIR": str(bin_dir),
            "ZED_PKG_REGISTRY": registry.resolve().as_uri(),
            "ZED_PKG_INTERACTIVE": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    env.pop("ZED_PKG_TOKEN", None)
    completed = subprocess.run(
        [str(zed), *args],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    evidence.append(
        {
            "argv": args,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    )
    if completed.returncode != expected:
        raise AssertionError(
            f"zed {' '.join(args)} returned {completed.returncode}, expected {expected}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def invoke_command(path: Path) -> str:
    if not path.is_file():
        raise AssertionError(f"global command was not materialized: {path}")
    if os.name != "nt" and not path.stat().st_mode & stat.S_IXUSR:
        raise AssertionError(f"global command is not executable: {path}")
    completed = subprocess.run(
        [str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"global command failed: {completed.returncode}\n{completed.stderr}"
        )
    return completed.stdout.strip()


def assert_no_credentials(root: Path) -> None:
    forbidden = ["token", "password", "authorization", "credential"]
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="ignore").lower()
        except OSError:
            continue
        for marker in forbidden:
            if marker in text:
                raise AssertionError(f"credential marker {marker!r} appeared in {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    options = parser.parse_args()

    zed = options.zed.resolve()
    if not zed.is_file():
        raise SystemExit(f"zed binary does not exist: {zed}")

    work = options.work.resolve()
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    registry = work / "registry"
    home = work / "home"
    bin_dir = work / "bin"
    evidence_commands: list[dict[str, Any]] = []
    fixtures = [publish_fixture(registry, "alpha"), publish_fixture(registry, "beta")]

    # First installation and executable invocation.
    run(
        zed,
        home,
        bin_dir,
        registry,
        ["global", "install", f"canary/alpha@{VERSION}"],
        evidence_commands,
    )
    command = executable_path(bin_dir)
    if invoke_command(command) != f"canary/alpha:{VERSION}":
        raise AssertionError("the installed command did not come from alpha")
    listed = run(
        zed,
        home,
        bin_dir,
        registry,
        ["global", "list"],
        evidence_commands,
    )
    if "canary/alpha@1.0.0" not in listed.stdout or f"bins: {COMMAND}" not in listed.stdout:
        raise AssertionError(f"global list omitted the installed package or command:\n{listed.stdout}")

    # A second package with the same command must fail before changing PATH and
    # must not leave a ghost top-level profile behind.
    collision = run(
        zed,
        home,
        bin_dir,
        registry,
        ["global", "install", f"canary/beta@{VERSION}"],
        evidence_commands,
        expected=1,
    )
    if "collision" not in collision.stderr.lower():
        raise AssertionError(f"collision failure was not explicit:\n{collision.stderr}")
    if profile_path(home, "beta").exists():
        raise AssertionError("failed collision install left a ghost beta profile")
    if invoke_command(command) != f"canary/alpha:{VERSION}":
        raise AssertionError("failed collision install changed the active command")

    # Remove profile materialization and the registry, then restore exactly from
    # the existing lock and content-addressed store.
    shutil.rmtree(profile_path(home, "alpha") / "zed_modules")
    command.unlink()
    shutil.rmtree(registry)
    run(
        zed,
        home,
        bin_dir,
        registry,
        ["global", "install", "--frozen", "canary/alpha"],
        evidence_commands,
    )
    if invoke_command(command) != f"canary/alpha:{VERSION}":
        raise AssertionError("frozen store-only restore produced the wrong command")

    # Modified commands are not deleted during uninstall.
    command.write_text("#!/bin/sh\nprintf '%s\\n' 'user replacement'\n")
    command.chmod(0o755)
    uninstalled = run(
        zed,
        home,
        bin_dir,
        registry,
        ["global", "uninstall", "canary/alpha"],
        evidence_commands,
    )
    if profile_path(home, "alpha").exists():
        raise AssertionError("uninstall did not remove the alpha profile")
    if invoke_command(command) != "user replacement":
        raise AssertionError("uninstall deleted or replaced a user-modified command")
    if "changed after Zed installed it" not in uninstalled.stderr:
        raise AssertionError("tamper-preserving uninstall did not emit its warning")

    # A clean second installation proves unchanged owned commands are removed.
    clean_registry = work / "clean-registry"
    clean_home = work / "clean-home"
    clean_bin = work / "clean-bin"
    publish_fixture(clean_registry, "alpha")
    run(
        zed,
        clean_home,
        clean_bin,
        clean_registry,
        ["install", "--global", f"canary/alpha@{VERSION}"],
        evidence_commands,
    )
    clean_command = executable_path(clean_bin)
    if invoke_command(clean_command) != f"canary/alpha:{VERSION}":
        raise AssertionError("compatibility install route produced the wrong command")
    run(
        zed,
        clean_home,
        clean_bin,
        clean_registry,
        ["uninstall", "--global", "canary/alpha"],
        evidence_commands,
    )
    if clean_command.exists() or profile_path(clean_home, "alpha").exists():
        raise AssertionError("clean uninstall retained Zed-owned state")

    assert_no_credentials(work)
    result = {
        "schema": "zed.global-package-profile-canary/v1",
        "zed_sha256": sha256(zed.read_bytes()),
        "fixtures": fixtures,
        "commands": evidence_commands,
        "checks": {
            "first_install_and_execute": True,
            "offline_profile_listing": True,
            "collision_is_explicit": True,
            "collision_rolls_back_profile": True,
            "collision_preserves_active_command": True,
            "frozen_store_only_restore": True,
            "tampered_command_preserved": True,
            "unchanged_command_removed": True,
            "credential_markers_absent": True,
        },
    }
    options.evidence.parent.mkdir(parents=True, exist_ok=True)
    options.evidence.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print("global executable package profile canary passed")


if __name__ == "__main__":
    main()
