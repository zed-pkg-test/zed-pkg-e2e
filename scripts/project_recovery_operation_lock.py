#!/usr/bin/env python3
"""Certify pending transaction recovery shares checkout-local ownership."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Sequence

from git_submodule_overtake_lock import Contract, ContractError

TRANSACTION_ID = "11111111-2222-4333-8444-555555555555"
BACKUP_RELATIVE = "backups/0000-66666666-7777-4888-8999-aaaaaaaaaaaa"
MARKER_RELATIVE = "recovery-marker.txt"
ORIGINAL = b"authoritative pre-crash bytes\n"
INTERRUPTED = b"interrupted replacement bytes\n"


def create_pending_recovery(project: Path) -> tuple[Path, Path, Path]:
    staging = project / ".zpkg-staging" / TRANSACTION_ID
    backup = staging / BACKUP_RELATIVE
    marker = project / MARKER_RELATIVE
    backup.parent.mkdir(parents=True)
    marker.write_bytes(INTERRUPTED)
    backup.write_bytes(ORIGINAL)
    metadata = {
        "id": TRANSACTION_ID,
        "state": "active",
        "entries": [
            {
                "relative": MARKER_RELATIVE,
                "backup": BACKUP_RELATIVE,
                "existed": True,
            }
        ],
    }
    (staging / "transaction.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return staging, backup, marker


def certify_different_home_waiter(contract: Contract) -> tuple[list[str], dict[str, float | int]]:
    _, root = contract.fixture("recovery")

    # The holder reaches Git synchronization only after acquiring the canonical
    # checkout-local operation lock. Create the interrupted journal afterward so
    # its own pre-Git recovery cannot consume this fixture.
    holder, _, release = contract.blocked_holder(root, "recovery")
    staging, backup, marker = create_pending_recovery(root)

    started = time.monotonic()
    waiter = contract.start(
        contract.zed_command("recovery-waiter", "install", "--frozen"),
        cwd=root,
    )
    contract.require_blocked(waiter)
    blocked_for = time.monotonic() - started

    # A waiter using a different Zed home must block before eager recovery. The
    # old Store-home lock allowed it to restore this journal concurrently with
    # the active takeover.
    if not staging.is_dir():
        raise ContractError("pending recovery ran while another mutation owned the checkout")
    if marker.read_bytes() != INTERRUPTED:
        raise ContractError("recovery marker changed before checkout ownership was released")
    if backup.read_bytes() != ORIGINAL:
        raise ContractError("recovery backup changed before checkout ownership was released")

    release.write_text("release\n", encoding="utf-8")
    holder_output = contract.finish(holder, expected=0)
    waiter_output = contract.finish(waiter, expected=0)
    if "overtook 1 Git submodule package(s)" not in holder_output:
        raise ContractError("takeover holder did not complete adoption")
    if "error:" in waiter_output.lower():
        raise ContractError("waiting frozen install reported an error")

    if marker.read_bytes() != ORIGINAL:
        raise ContractError("pending recovery did not restore the exact backup bytes")
    if (root / ".zpkg-staging").exists():
        raise ContractError("successful recovery left project staging state")
    if not (root / ".zpkg.lock").is_file():
        raise ContractError("takeover did not publish the lockfile after recovery")
    if not (root / "zed_modules/acme/recovery-child/payload.txt").is_file():
        raise ContractError("waiting frozen install did not materialize adopted state")

    checks = [
        "different-home eager recovery blocks behind active checkout mutation",
        "pending journal and backup remain byte-exact while ownership is held",
        "recovery restores exact bytes and removes staging after release",
        "recovered process completes frozen install against adopted state",
    ]
    process = {
        "holder_pid": holder.pid,
        "waiter_pid": waiter.pid,
        "observed_block_seconds": round(blocked_for, 3),
    }
    return checks, process


def certify_overtake_recovers_before_git(contract: Contract) -> tuple[list[str], int]:
    _, root = contract.fixture("preflight-recovery")
    staging, _, marker = create_pending_recovery(root)

    # Modular takeover dispatch bypasses main's ordinary run() entrypoint. By
    # the time the Git shim announces `submodule sync`, recovery must already
    # have completed under the same project operation lock.
    holder, _, release = contract.blocked_holder(root, "preflight-recovery")
    if staging.exists():
        raise ContractError("takeover reached Git sync before recovering pending state")
    if marker.read_bytes() != ORIGINAL:
        raise ContractError("takeover did not restore exact bytes before Git sync")

    release.write_text("release\n", encoding="utf-8")
    output = contract.finish(holder, expected=0)
    if "overtook 1 Git submodule package(s)" not in output:
        raise ContractError("recovered takeover did not complete adoption")
    if not (root / ".zpkg.lock").is_file():
        raise ContractError("recovered takeover did not publish a lockfile")

    checks = [
        "modular takeover recovers pending transaction before Git transport",
        "pre-Git recovery restores exact bytes before adoption begins",
    ]
    return checks, holder.pid


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    contract = Contract(args.zed, args.work_root)
    blocked_checks, blocked_process = certify_different_home_waiter(contract)
    pre_git_checks, pre_git_holder_pid = certify_overtake_recovers_before_git(contract)
    checks = [*blocked_checks, *pre_git_checks]
    if len(checks) != 6:
        raise ContractError(f"unexpected recovery check count: {checks}")

    evidence = {
        "schema": "zed.project-recovery-operation-lock/v1",
        "checks": checks,
        "different_home_waiter": blocked_process,
        "pre_git_holder_pid": pre_git_holder_pid,
        "network_credentials": False,
        "public_registry_mutation": False,
    }
    (contract.evidence / "recovery-evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    contract.log("\ncertified 6 project-recovery ownership checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
