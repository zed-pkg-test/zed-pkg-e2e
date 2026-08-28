#!/usr/bin/env python3
"""Certify that nested takeover locks the discovered superproject root."""

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
    _, root = contract.fixture("nested")

    nested = root / "packages/client/src"
    nested.mkdir(parents=True)
    ready = contract.runs / "nested.ready"
    release = contract.runs / "nested.release"
    path = f"{contract.shim_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    holder_env = {
        "PATH": path,
        "ZED_TEST_BLOCK_SUBMODULE_SYNC": "1",
        "ZED_TEST_READY": str(ready),
        "ZED_TEST_RELEASE": str(release),
    }

    holder = contract.start(
        contract.zed_command(
            "nested-holder", "overtake", "--git-submodules"
        ),
        cwd=nested,
        env=holder_env,
    )
    contract.wait_ready(ready, holder)

    root_lock = root / ".zed/operation.lock"
    nested_lock = nested / ".zed/operation.lock"
    if not root_lock.is_file():
        raise ContractError(
            "nested takeover reached Git sync without owning the superproject lock"
        )
    if nested_lock.exists():
        raise ContractError(
            "nested takeover created a second invocation-directory operation lock"
        )

    waiter_started = time.monotonic()
    waiter = contract.start(
        contract.zed_command("nested-waiter", "install", "--frozen"),
        cwd=root,
    )
    contract.require_blocked(waiter)
    blocked_for = time.monotonic() - waiter_started

    release.write_text("release\n", encoding="utf-8")
    holder_output = contract.finish(holder, expected=0)
    waiter_output = contract.finish(waiter, expected=0)
    if "overtook 1 Git submodule package(s)" not in holder_output:
        raise ContractError("nested takeover did not report one adopted package")
    if "error:" in waiter_output.lower():
        raise ContractError("root-level waiter reported an error")
    if not (root / ".zpkg.lock").is_file():
        raise ContractError("nested takeover did not publish a root lockfile")
    if not (root / "zed_modules/acme/nested-child/payload.txt").is_file():
        raise ContractError("root-level frozen waiter did not materialize the package")

    checks = [
        "nested takeover owns the discovered superproject operation lock",
        "nested takeover does not create an invocation-directory lock identity",
        "root-level frozen install blocks and succeeds behind nested takeover",
    ]
    evidence = {
        "schema": "zed.git-submodule-nested-overtake-lock/v1",
        "checks": checks,
        "holder_pid": holder.pid,
        "waiter_pid": waiter.pid,
        "observed_block_seconds": round(blocked_for, 3),
        "network_credentials": False,
        "public_registry_mutation": False,
    }
    (contract.evidence / "nested-evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    contract.log("\ncertified 3 nested takeover lock-identity checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
