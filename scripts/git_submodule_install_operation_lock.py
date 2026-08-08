#!/usr/bin/env python3
"""Certify `zed install --git-submodules` owns one complete mutation boundary."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Sequence

from git_submodule_overtake_lock import Contract, ContractError


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    contract = Contract(args.zed, args.work_root)
    _, root = contract.fixture("cooperative-install")

    ready = contract.runs / "install.ready"
    release = contract.runs / "install.release"
    path = f"{contract.shim_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    holder_env = {
        "PATH": path,
        "ZED_TEST_BLOCK_SUBMODULE_SYNC": "1",
        "ZED_TEST_READY": str(ready),
        "ZED_TEST_RELEASE": str(release),
    }
    holder = contract.start(
        contract.zed_command(
            "install-holder", "install", "--git-submodules"
        ),
        cwd=root,
        env=holder_env,
    )
    contract.wait_ready(ready, holder)

    lock_path = root / ".zed/operation.lock"
    if not lock_path.is_file():
        raise ContractError(
            "cooperative install reached Git sync before owning operation.lock"
        )
    if (root / "vendor/child/.zpkg.toml").exists():
        raise ContractError("Git submodule update ran before the test release")
    if (root / ".zpkg.lock").exists():
        raise ContractError("install published a lockfile before Git sync was released")

    started = time.monotonic()
    waiter = contract.start(
        contract.zed_command("install-waiter", "install", "--frozen"),
        cwd=root,
    )
    contract.require_blocked(waiter)
    blocked_for = time.monotonic() - started
    if (root / ".zpkg.lock").exists():
        raise ContractError("frozen waiter raced ahead of cooperative install")

    release.write_text("release\n", encoding="utf-8")
    holder_output = contract.finish(holder, expected=0)
    waiter_output = contract.finish(waiter, expected=0)
    if "synchronized 1 Git submodule(s)" not in holder_output:
        raise ContractError("cooperative install did not report submodule synchronization")
    if "error:" in holder_output.lower() or "error:" in waiter_output.lower():
        raise ContractError("serialized install process reported an error")
    if not (root / ".zpkg.lock").is_file():
        raise ContractError("cooperative install did not publish a lockfile")
    if not (root / "vendor/child/.zpkg.toml").is_file():
        raise ContractError("cooperative install did not initialize the child submodule")
    if (
        root / "vendor/child/payload.txt"
    ).read_text(encoding="utf-8") != "payload for cooperative-install\n":
        raise ContractError("initialized child payload did not match the pinned gitlink")

    checks = [
        "cooperative install owns operation.lock before Git submodule sync",
        "different-home frozen install blocks across Git sync and installation",
        "blocked frozen install succeeds only after complete state publication",
    ]
    evidence = {
        "schema": "zed.git-submodule-install-operation-lock/v1",
        "checks": checks,
        "holder_pid": holder.pid,
        "waiter_pid": waiter.pid,
        "observed_block_seconds": round(blocked_for, 3),
        "network_credentials": False,
        "public_registry_mutation": False,
    }
    (contract.evidence / "install-evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    contract.log("\ncertified 3 cooperative-install ownership checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
