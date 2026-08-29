#!/usr/bin/env python3
"""Independent persisted-bundle verifier and tamper matrix for Zed → Nix."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA = "zed.nix-flake-bundle/v1"
INVENTORY_PATH = "metadata/bundle.json"
EXPECTED_PATHS = {
    "README.md",
    "artifacts/example-sample-1.2.3.tar.gz",
    "flake.lock",
    "flake.nix",
    "metadata/bundle.json",
    "metadata/plan.json",
    "package.nix",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def safe_relative_path(value: str) -> PurePosixPath:
    if not value or value.startswith("/") or "\\" in value:
        raise AssertionError("bundle contains an unsafe path")
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise AssertionError("bundle contains an unsafe path component")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise AssertionError("bundle contains a control-bearing path")
    return path


def regular_files(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise AssertionError("persisted bundle contains a symbolic link")
        if path.is_dir():
            continue
        if not path.is_file():
            raise AssertionError("persisted bundle contains a special file")
        safe_relative_path(relative)
        if stat.S_IMODE(path.stat().st_mode) != 0o644:
            raise AssertionError("persisted bundle file mode is not canonical")
        files[relative] = path.read_bytes()
    return files


def bundle_digest(entries: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    digest.update(SCHEMA.encode("utf-8"))
    digest.update(b"\0")
    for entry in entries:
        path = str(entry["path"])
        path_bytes = path.encode("utf-8")
        artifact_digest = bytes.fromhex(str(entry["sha256"]))
        if len(artifact_digest) != 32:
            raise AssertionError("bundle entry digest length is invalid")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(int(entry["size"]).to_bytes(8, "big"))
        digest.update(artifact_digest)
    return digest.hexdigest()


def verify_bundle(root: Path, forbidden: tuple[str, ...]) -> dict[str, Any]:
    if not root.is_dir():
        raise AssertionError("bundle root is missing")
    files = regular_files(root)
    if set(files) != EXPECTED_PATHS:
        raise AssertionError("bundle file set differs from the strict v1 contract")

    inventory_bytes = files[INVENTORY_PATH]
    inventory = json.loads(inventory_bytes)
    if canonical_json(inventory) != inventory_bytes:
        raise AssertionError("bundle inventory is not canonical compact JSON")
    if inventory.get("schema") != SCHEMA:
        raise AssertionError("bundle schema is unsupported")

    entries = inventory.get("entries")
    if not isinstance(entries, list):
        raise AssertionError("bundle inventory entries are missing")
    paths = [entry.get("path") for entry in entries]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise AssertionError("bundle inventory paths are not sorted and unique")
    if paths != sorted(set(files) - {INVENTORY_PATH}):
        raise AssertionError("bundle inventory does not cover the exact file set")

    for entry in entries:
        path = str(entry["path"])
        safe_relative_path(path)
        data = files[path]
        if int(entry["size"]) != len(data):
            raise AssertionError("bundle entry size evidence does not match")
        if str(entry["sha256"]) != sha256(data):
            raise AssertionError("bundle entry digest evidence does not match")

    if inventory.get("bundle_sha256") != bundle_digest(entries):
        raise AssertionError("domain-separated bundle digest does not match")
    if inventory.get("plan_sha256") != sha256(files["metadata/plan.json"]):
        raise AssertionError("plan digest does not match")
    if inventory.get("flake_lock_sha256") != sha256(files["flake.lock"]):
        raise AssertionError("flake lock digest does not match")

    for value in forbidden:
        if not value:
            continue
        needle = value.encode("utf-8")
        if any(needle in data for data in files.values()):
            raise AssertionError("bundle retained forbidden ambient input")
    return inventory


def assert_expected_failure(root: Path, mutate) -> None:
    candidate = root.parent / f"{root.name}-tamper"
    if candidate.exists():
        shutil.rmtree(candidate)
    shutil.copytree(root, candidate)
    mutate(candidate)
    try:
        verify_bundle(candidate, ())
    except (AssertionError, json.JSONDecodeError, KeyError, ValueError):
        shutil.rmtree(candidate)
        return
    raise AssertionError("tampered persisted bundle unexpectedly verified")


def exercise_tamper_matrix(root: Path) -> None:
    assert_expected_failure(
        root,
        lambda candidate: (candidate / "package.nix").open("ab").write(b"\n# tampered\n"),
    )
    assert_expected_failure(
        root,
        lambda candidate: (candidate / "artifacts/example-sample-1.2.3.tar.gz").unlink(),
    )
    assert_expected_failure(
        root,
        lambda candidate: (candidate / "unexpected.txt").write_text(
            "unexpected", encoding="utf-8"
        ),
    )
    assert_expected_failure(
        root,
        lambda candidate: (candidate / "flake.lock").open("ab").write(b"\n"),
    )

    def rewrite_inventory(candidate: Path) -> None:
        path = candidate / INVENTORY_PATH
        value = json.loads(path.read_bytes())
        path.write_text(json.dumps(value, indent=2), encoding="utf-8")

    assert_expected_failure(root, rewrite_inventory)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-a", required=True, type=Path)
    parser.add_argument("--bundle-b", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--forbidden", action="append", default=[])
    parser.add_argument("--exercise-tamper", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    bundle_a = args.bundle_a.resolve()
    bundle_b = args.bundle_b.resolve()
    report_path = args.report.resolve()
    forbidden = tuple(args.forbidden)

    inventory_a = verify_bundle(bundle_a, forbidden)
    inventory_b = verify_bundle(bundle_b, forbidden)
    if regular_files(bundle_a) != regular_files(bundle_b):
        raise AssertionError("clean-room bundle replays are not byte-identical")
    if inventory_a != inventory_b:
        raise AssertionError("clean-room bundle inventories differ")

    report = json.loads(report_path.read_bytes())
    if report.get("schema") != "zed-pkg-test.nix-flake-bundle-canary/v1":
        raise AssertionError("external canary report schema is unsupported")
    if report.get("bundle_sha256") != inventory_a.get("bundle_sha256"):
        raise AssertionError("external report is not bound to the bundle inventory")
    if report.get("plan_sha256") != inventory_a.get("plan_sha256"):
        raise AssertionError("external report is not bound to the plan")
    if report.get("flake_lock_sha256") != inventory_a.get("flake_lock_sha256"):
        raise AssertionError("external report is not bound to the flake lock")
    if report.get("credential_canaries_retained") is not False:
        raise AssertionError("external report did not prove credential redaction")
    if report.get("external_registry_required") is not False:
        raise AssertionError("external report unexpectedly requires a registry")

    if args.exercise_tamper:
        exercise_tamper_matrix(bundle_a)

    print(
        json.dumps(
            {
                "result": "PASS",
                "schema": report["schema"],
                "candidate": report.get("candidate", ""),
                "system": report["system"],
                "bundle_sha256": report["bundle_sha256"],
                "file_count": report["file_count"],
                "tamper_cases": 5 if args.exercise_tamper else 0,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
