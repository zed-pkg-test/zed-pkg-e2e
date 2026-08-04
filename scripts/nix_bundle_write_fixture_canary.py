#!/usr/bin/env python3
"""Certify Nix bundle writing against an immutable real package fixture."""

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

NIXPKGS_REV = "e73de5be04e0eff4190a1432b946d469c794e7b4"
NIXPKGS_NAR_HASH = "sha256-pGvFkM8N0xEkIIXDe5YYfbEAvHrk4IxBrjB/x8OomhE="


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(
    argv: Sequence[str | Path],
    *,
    cwd: Path,
    env: Mapping[str, str],
    expect_success: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = [str(value) for value in argv]
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=dict(env),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    print(
        f"$ (cd {cwd} && {' '.join(command)})\n"
        f"exit={completed.returncode}\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}\n",
        flush=True,
    )
    if expect_success and completed.returncode != 0:
        raise AssertionError(f"command failed: {' '.join(command)}")
    if not expect_success and completed.returncode == 0:
        raise AssertionError(f"command unexpectedly succeeded: {' '.join(command)}")
    return completed


def clean_environment(secret: str, home: Path) -> dict[str, str]:
    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith("ZED_PKG_"):
            environment.pop(key, None)
    environment.update(
        {
            "NIX_CONFIG": (
                "experimental-features = nix-command flakes\n"
                "accept-flake-config = false\n"
            ),
            "ZED_PKG_TOKEN": secret,
            "ZED_PKG_SUPABASE_KEY": secret,
            "ZED_PKG_AUTH_PASSWORD": secret,
            "ZED_PKG_HOME": str(home),
            "ZED_PKG_REGISTRY": "https://person:secret@example.invalid/private",
        }
    )
    return environment


def git_clean(fixture: Path) -> None:
    completed = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=fixture,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    if completed.stdout.strip():
        raise AssertionError(f"fixture checkout is dirty:\n{completed.stdout}")


def copy_fixture(fixture: Path, destination: Path, system: str) -> None:
    shutil.copytree(
        fixture,
        destination,
        ignore=shutil.ignore_patterns(
            ".git",
            ".zed",
            ".zpkg-staging",
            "node_modules",
            "zed_modules",
        ),
    )
    manifest = destination / ".zpkg.toml"
    existing = manifest.read_text(encoding="utf-8")
    manifest.write_text(
        existing
        + "\n[publish.nix]\n"
        + 'attribute = "node_lib"\n'
        + f'systems = ["{system}"]\n'
        + 'outputs = ["out"]\n',
        encoding="utf-8",
    )
    (destination / ".zpkg.lock").write_text("version = 1\n", encoding="utf-8")
    (destination / "nested" / "deep").mkdir(parents=True)


def flake_lock() -> bytes:
    return json.dumps(
        {
            "nodes": {
                "nixpkgs": {
                    "locked": {
                        "lastModified": 1782467914,
                        "narHash": NIXPKGS_NAR_HASH,
                        "owner": "NixOS",
                        "repo": "nixpkgs",
                        "rev": NIXPKGS_REV,
                        "type": "github",
                    },
                    "original": {
                        "owner": "NixOS",
                        "repo": "nixpkgs",
                        "rev": NIXPKGS_REV,
                        "type": "github",
                    },
                },
                "root": {"inputs": {"nixpkgs": "nixpkgs"}},
            },
            "root": "root",
            "version": 7,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def snapshot(root: Path) -> dict[str, tuple[str, int]]:
    result: dict[str, tuple[str, int]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise AssertionError(f"unexpected symlink in snapshot: {relative}")
        if path.is_file():
            result[relative] = (sha256(path.read_bytes()), path.stat().st_mode & 0o777)
    return result


def write_bundle(
    zed: Path,
    project: Path,
    approved_lock: Path,
    output: Path,
    environment: Mapping[str, str],
) -> dict:
    completed = run(
        [
            zed,
            "interop",
            "nix",
            "bundle",
            "write",
            "--frozen",
            "--flake-lock",
            approved_lock,
            "--out",
            output,
            "--json",
        ],
        cwd=project / "nested" / "deep",
        env=environment,
    )
    try:
        receipt = json.loads(completed.stdout.strip())
    except json.JSONDecodeError as error:
        raise AssertionError(f"invalid bundle receipt: {completed.stdout!r}") from error
    if receipt.get("schema") != "zed.nix-flake-bundle-write/v1":
        raise AssertionError(receipt)
    if receipt.get("outcome") != "created":
        raise AssertionError(receipt)
    return receipt


def validate_bundle(
    bundle: Path,
    project: Path,
    secret: str,
    unused_home: Path,
) -> dict:
    expected = {
        "README.md",
        "flake.lock",
        "flake.nix",
        "package.nix",
        "metadata/bundle.json",
        "metadata/plan.json",
        "artifacts/zed-pkg-test-node-lib-1.0.0.tar.gz",
    }
    actual = set(snapshot(bundle))
    if actual != expected:
        raise AssertionError(f"unexpected bundle files: {sorted(actual)}")

    plan_raw = (bundle / "metadata" / "plan.json").read_text(encoding="utf-8")
    inventory_raw = (bundle / "metadata" / "bundle.json").read_text(
        encoding="utf-8"
    )
    textual = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(bundle.rglob("*"))
        if path.is_file() and path.suffix not in {".gz"}
    )
    for forbidden in (
        str(project),
        str(project.parent),
        str(unused_home),
        secret,
        "person:secret",
        "example.invalid",
    ):
        if forbidden in textual:
            raise AssertionError(f"bundle leaked forbidden value: {forbidden!r}")

    plan = json.loads(plan_raw)
    if plan["schema"] != "zed.nix-export-plan/v1":
        raise AssertionError(plan)
    if plan["package"] != {
        "org": "zed-pkg-test",
        "name": "node-lib",
        "version": "1.0.0",
        "target": None,
    }:
        raise AssertionError(plan["package"])
    if plan["package_class"] != "data":
        raise AssertionError(plan)
    if plan["intent"]["attribute"] != "node_lib":
        raise AssertionError(plan)
    if plan["dependencies"] != [] or plan["bins"] != {}:
        raise AssertionError(plan)

    artifact = bundle / "artifacts" / plan["source"]["file_name"]
    artifact_bytes = artifact.read_bytes()
    if sha256(artifact_bytes) != plan["source"]["artifact"]["sha256"]:
        raise AssertionError("artifact digest differs from plan")
    if len(artifact_bytes) != plan["source"]["artifact"]["size"]:
        raise AssertionError("artifact size differs from plan")

    inventory = json.loads(inventory_raw)
    if inventory["schema"] != "zed.nix-flake-bundle/v1":
        raise AssertionError(inventory)
    if inventory["nixpkgs"]["rev"] != NIXPKGS_REV:
        raise AssertionError(inventory)
    if inventory["nixpkgs"]["nar_hash"] != NIXPKGS_NAR_HASH:
        raise AssertionError(inventory)
    if len(inventory["bundle_sha256"]) != 64:
        raise AssertionError(inventory)

    entries = inventory["entries"]
    paths = [entry["path"] for entry in entries]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise AssertionError("bundle inventory paths are not sorted and unique")
    for entry in entries:
        path = bundle / entry["path"]
        data = path.read_bytes()
        if sha256(data) != entry["sha256"] or len(data) != entry["size"]:
            raise AssertionError(f"inventory mismatch for {entry['path']}")
    return inventory


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", required=True, type=Path)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    args = parser.parse_args()

    zed = args.zed.resolve()
    fixture = args.fixture.resolve()
    root = args.work_root.resolve()
    if root.exists():
        raise AssertionError(f"work root must be fresh: {root}")
    if not zed.is_file():
        raise FileNotFoundError(zed)
    if not (fixture / ".zpkg.toml").is_file():
        raise FileNotFoundError(fixture / ".zpkg.toml")
    git_clean(fixture)
    root.mkdir(parents=True)

    nix_environment = os.environ.copy()
    nix_environment["NIX_CONFIG"] = (
        "experimental-features = nix-command flakes\n"
        "accept-flake-config = false\n"
    )
    system = run(
        ["nix", "eval", "--impure", "--raw", "--expr", "builtins.currentSystem"],
        cwd=root,
        env=nix_environment,
    ).stdout.strip()
    if not system:
        raise AssertionError("Nix returned an empty current system")

    first_project = root / "different" / "absolute" / "first-project"
    second_project = root / "other" / "location" / "second-project"
    copy_fixture(fixture, first_project, system)
    copy_fixture(fixture, second_project, system)
    first_before = snapshot(first_project)
    second_before = snapshot(second_project)

    approved_lock = root / "approved-flake.lock"
    approved_lock.write_bytes(flake_lock())
    outputs = root / "exports"
    outputs.mkdir()
    first_bundle = outputs / "first"
    second_bundle = outputs / "second"
    unused_home = root / "zed-home-must-not-exist"
    secret = "real-fixture-bundle-secret-must-not-appear"
    environment = clean_environment(secret, unused_home)

    first_receipt = write_bundle(
        zed,
        first_project,
        approved_lock,
        first_bundle,
        environment,
    )
    second_receipt = write_bundle(
        zed,
        second_project,
        approved_lock,
        second_bundle,
        environment,
    )
    first_inventory = validate_bundle(
        first_bundle,
        first_project,
        secret,
        unused_home,
    )
    second_inventory = validate_bundle(
        second_bundle,
        second_project,
        secret,
        unused_home,
    )

    if snapshot(first_bundle) != snapshot(second_bundle):
        raise AssertionError("identical real fixtures produced different bundle bytes")
    if first_receipt["bundle_sha256"] != second_receipt["bundle_sha256"]:
        raise AssertionError("identical real fixtures produced different bundle identities")
    if first_inventory["bundle_sha256"] != second_inventory["bundle_sha256"]:
        raise AssertionError("bundle inventories differ across absolute project paths")
    if snapshot(first_project) != first_before or snapshot(second_project) != second_before:
        raise AssertionError("bundle writing mutated a real fixture copy")
    if unused_home.exists():
        raise AssertionError("bundle writing created the configured Zed home")
    git_clean(fixture)

    print(
        json.dumps(
            {
                "schema": "zed.nix-bundle-write-fixture-canary/v1",
                "package": "zed-pkg-test/node-lib@1.0.0",
                "system": system,
                "bundle_sha256": first_inventory["bundle_sha256"],
                "result": "pass",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
