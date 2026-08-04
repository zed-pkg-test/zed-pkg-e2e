#!/usr/bin/env python3
"""Independent black-box acceptance for `zed interop nix bundle write`.

The harness owns a fresh project, approved immutable flake lock, output roots,
and Zed home. It invokes only the compiled CLI and Nix executables; it imports
no implementation module from zed-cli.
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
from typing import Mapping, Sequence

SCHEMA = "zed.nix-flake-bundle-write/v1"
NIXPKGS_REV = "e73de5be04e0eff4190a1432b946d469c794e7b4"
NIXPKGS_NAR_HASH = "sha256-pGvFkM8N0xEkIIXDe5YYfbEAvHrk4IxBrjB/x8OomhE="


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(
    argv: Sequence[str | Path],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    expect_success: bool = True,
    log: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [str(value) for value in argv]
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    transcript = (
        f"$ (cd {cwd} && {' '.join(command)})\n"
        f"exit={completed.returncode}\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}\n"
    )
    print(transcript, flush=True)
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(transcript)
    if expect_success and completed.returncode != 0:
        raise AssertionError(f"command failed: {' '.join(command)}")
    if not expect_success and completed.returncode == 0:
        raise AssertionError(f"command unexpectedly succeeded: {' '.join(command)}")
    return completed


def snapshot(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not root.exists():
        return result
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            result[path.relative_to(root).as_posix()] = f"symlink:{os.readlink(path)}"
        elif path.is_file():
            result[path.relative_to(root).as_posix()] = sha256(path.read_bytes())
    return result


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


def manifest(system: str) -> str:
    return f'''[package]
org = "acme"
name = "dataset"
version = "1.2.3"
description = "immutable data fixture"
license = "MIT"

[package.repository]
url = "https://github.com/acme/dataset"

[publish.nix]
attribute = "dataset"
systems = ["{system}"]
outputs = ["out"]
'''


def create_project(project: Path, system: str) -> None:
    (project / "src" / "deep").mkdir(parents=True)
    (project / "data").mkdir()
    (project / ".zpkg.toml").write_text(manifest(system), encoding="utf-8")
    (project / ".zpkg.lock").write_text("version = 1\n", encoding="utf-8")
    (project / "data" / "value.txt").write_bytes(b"same immutable payload\n")


def clean_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith("ZED_PKG_"):
            environment.pop(key, None)
    environment["NIX_CONFIG"] = (
        "experimental-features = nix-command flakes\n"
        "accept-flake-config = false\n"
    )
    return environment


def bundle_command(
    zed: Path,
    zed_home: Path,
    arguments: Sequence[str | Path],
) -> list[str | Path]:
    return [
        zed,
        "--registry",
        "https://registry.invalid.example",
        "--home",
        zed_home,
        "interop",
        "nix",
        "bundle",
        "write",
        *arguments,
    ]


def assert_bundle(bundle: Path, project: Path, zed_home: Path) -> None:
    expected = {
        "flake.nix",
        "flake.lock",
        "package.nix",
        "README.md",
        "metadata/plan.json",
        "metadata/bundle.json",
        "artifacts/acme-dataset-1.2.3.tar.gz",
    }
    actual = {
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*")
        if path.is_file()
    }
    if actual != expected:
        raise AssertionError(f"unexpected bundle inventory: {sorted(actual)}")

    textual = "\n".join(
        (bundle / relative).read_text(encoding="utf-8")
        for relative in sorted(expected)
        if not relative.endswith(".tar.gz")
    )
    for forbidden in (
        str(project),
        str(zed_home),
        "registry.invalid.example",
    ):
        if forbidden in textual:
            raise AssertionError(f"bundle leaked forbidden value: {forbidden}")

    inventory = json.loads((bundle / "metadata" / "bundle.json").read_text())
    if inventory["schema"] != "zed.nix-flake-bundle/v1":
        raise AssertionError(inventory)
    if inventory["nixpkgs"]["rev"] != NIXPKGS_REV:
        raise AssertionError(inventory)
    if inventory["nixpkgs"]["nar_hash"] != NIXPKGS_NAR_HASH:
        raise AssertionError(inventory)
    if len(inventory["bundle_sha256"]) != 64:
        raise AssertionError(inventory)


def receipt(output: subprocess.CompletedProcess[str]) -> dict:
    try:
        parsed = json.loads(output.stdout.strip())
    except json.JSONDecodeError as error:
        raise AssertionError(f"invalid receipt JSON: {output.stdout!r}") from error
    if parsed.get("schema") != SCHEMA:
        raise AssertionError(parsed)
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    args = parser.parse_args()

    zed = args.zed.resolve()
    root = args.work_root.resolve()
    if not zed.is_file():
        raise FileNotFoundError(zed)
    if root.exists():
        raise AssertionError(f"work root must be fresh: {root}")
    root.mkdir(parents=True)

    log = root / "diagnostics" / "canary.log"
    environment = clean_environment()
    system_result = run(
        ["nix", "eval", "--impure", "--raw", "--expr", "builtins.currentSystem"],
        cwd=root,
        env=environment,
        log=log,
    )
    system = system_result.stdout.strip()
    if not system:
        raise AssertionError("Nix returned an empty current system")

    project = root / "project"
    approved_lock = root / "approved-flake.lock"
    output_parent = root / "exports"
    bundle = output_parent / "dataset-flake"
    zed_home = root / "must-not-create-zed-home"
    create_project(project, system)
    approved_lock.write_bytes(flake_lock())
    output_parent.mkdir()
    project_before = snapshot(project)

    first = run(
        bundle_command(
            zed,
            zed_home,
            [
                "--frozen",
                "--flake-lock",
                approved_lock,
                "--out",
                bundle,
                "--json",
            ],
        ),
        cwd=project / "src" / "deep",
        env=environment,
        log=log,
    )
    first_receipt = receipt(first)
    if first_receipt["outcome"] != "created":
        raise AssertionError(first_receipt)
    canonical_bundle = bundle.resolve()
    if first_receipt["destination"] != str(canonical_bundle):
        raise AssertionError(first_receipt)
    if first_receipt["package"] != {
        "org": "acme",
        "name": "dataset",
        "version": "1.2.3",
        "target": None,
    }:
        raise AssertionError(first_receipt)
    assert_bundle(bundle, project, zed_home)
    if snapshot(project) != project_before:
        raise AssertionError("bundle command mutated the source project")
    if zed_home.exists():
        raise AssertionError("credential-independent command created Zed home")
    bundle_before = snapshot(bundle)

    second = run(
        bundle_command(
            zed,
            zed_home,
            [
                "--frozen",
                "--flake-lock",
                approved_lock,
                "--output",
                bundle,
                "--json",
            ],
        ),
        cwd=project,
        env=environment,
        log=log,
    )
    second_receipt = receipt(second)
    if second_receipt["outcome"] != "already-current":
        raise AssertionError(second_receipt)
    if second_receipt["bundle_sha256"] != first_receipt["bundle_sha256"]:
        raise AssertionError("idempotent invocation changed bundle identity")
    if snapshot(bundle) != bundle_before:
        raise AssertionError("idempotent invocation rewrote bundle bytes")

    # Prove the command-produced directory is a real standalone flake. Network
    # acquisition is explicit and precedes the offline replay.
    run(
        ["nix", "flake", "archive", "--no-update-lock-file"],
        cwd=bundle,
        env=environment,
        log=log,
    )
    online_build = run(
        [
            "nix",
            "build",
            "--no-update-lock-file",
            "--no-link",
            "--print-out-paths",
            ".#dataset",
        ],
        cwd=bundle,
        env=environment,
        log=log,
    )
    run(
        ["nix", "flake", "check", "--offline", "--no-update-lock-file"],
        cwd=bundle,
        env=environment,
        log=log,
    )
    offline_build = run(
        [
            "nix",
            "build",
            "--offline",
            "--no-update-lock-file",
            "--no-link",
            "--print-out-paths",
            ".#dataset",
        ],
        cwd=bundle,
        env=environment,
        log=log,
    )
    if online_build.stdout.strip() != offline_build.stdout.strip():
        raise AssertionError("offline replay selected a different store output")
    if snapshot(bundle) != bundle_before:
        raise AssertionError("Nix verification rewrote the standalone bundle")

    env_bundle = output_parent / "environment-flake"
    env_only = environment.copy()
    env_only.update(
        {
            "ZED_PKG_FROZEN": "yes",
            "ZED_PKG_NIX_PLAN_JSON": "on",
            "ZED_PKG_NIX_FLAKE_LOCK": str(approved_lock),
            "ZED_PKG_NIX_BUNDLE_OUT": str(env_bundle),
        }
    )
    env_result = run(
        bundle_command(zed, zed_home, []),
        cwd=project,
        env=env_only,
        log=log,
    )
    if receipt(env_result)["outcome"] != "created":
        raise AssertionError(env_result.stdout)
    assert_bundle(env_bundle, project, zed_home)

    unknown = run(
        bundle_command(
            zed,
            zed_home,
            [
                "--frozen",
                "--flake-lock",
                approved_lock,
                "--out",
                output_parent / "unknown-flake",
                "--definitely-unknown",
            ],
        ),
        cwd=project,
        env=environment,
        expect_success=False,
        log=log,
    )
    if "unknown Nix interop option" not in unknown.stderr:
        raise AssertionError(unknown.stderr)
    if (output_parent / "unknown-flake").exists():
        raise AssertionError("unknown-option invocation wrote output")

    if os.name != "nt":
        real_parent = root / "real-exports"
        linked_parent = root / "linked-exports"
        real_parent.mkdir()
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        linked = run(
            bundle_command(
                zed,
                zed_home,
                [
                    "--frozen",
                    "--flake-lock",
                    approved_lock,
                    "--out",
                    linked_parent / "bundle",
                ],
            ),
            cwd=project,
            env=environment,
            expect_success=False,
            log=log,
        )
        if "is not a real directory" not in linked.stderr:
            raise AssertionError(linked.stderr)
        if (real_parent / "bundle").exists():
            raise AssertionError("symlink-parent invocation escaped output boundary")

    (bundle / "README.md").write_bytes(b"tampered\n")
    tampered = run(
        bundle_command(
            zed,
            zed_home,
            [
                "--frozen",
                "--flake-lock",
                approved_lock,
                "--out",
                bundle,
            ],
        ),
        cwd=project,
        env=environment,
        expect_success=False,
        log=log,
    )
    if "differs from rendered bytes" not in tampered.stderr:
        raise AssertionError(tampered.stderr)
    if (bundle / "README.md").read_bytes() != b"tampered\n":
        raise AssertionError("tamper rejection overwrote caller-owned bytes")

    print(
        json.dumps(
            {
                "schema": "zed.nix-bundle-write-canary/v1",
                "system": system,
                "cli": str(zed),
                "bundle_sha256": first_receipt["bundle_sha256"],
                "store_output": offline_build.stdout.strip(),
                "result": "pass",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
