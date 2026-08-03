#!/usr/bin/env python3
"""Fail-closed policy checks for the reusable zed-pkg-test candidate gate."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable

FULL_SHA = r"[0-9a-f]{40}"
REMOTE_USE = re.compile(r"(?m)^\s*(?:-\s*)?uses:\s*([^\s#]+)")


class ContractViolation(AssertionError):
    """Raised when certification procedure code drifts from its trust boundary."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractViolation(message)


def require_all(text: str, needles: Iterable[str], label: str) -> None:
    for needle in needles:
        require(needle in text, f"{label} is missing required contract text: {needle}")


def audit_workflow(text: str) -> None:
    """Audit the reusable workflow without evaluating pull-request code."""

    require("workflow_call:" in text, "candidate workflow is no longer reusable")
    require("pull_request_target:" not in text, "pull_request_target is forbidden")
    require(
        re.search(r"(?m)^permissions:\s*$\n\s{2}contents:\s*read\s*$", text)
        is not None,
        "workflow must declare top-level contents: read",
    )
    require(
        re.search(r"(?m)^\s{2}[a-zA-Z0-9_-]+:\s*write\s*$", text) is None,
        "workflow may not request any write permission",
    )
    require("secrets: inherit" not in text, "reusable caller secrets may not be inherited")
    require("${{ secrets." not in text, "candidate workflow may not read repository secrets")
    require("persist-credentials: true" not in text, "checkout credentials may not persist")

    uses = REMOTE_USE.findall(text)
    require(uses, "candidate workflow has no auditable uses entries")
    for target in uses:
        if target.startswith("./") or target.startswith("docker://"):
            continue
        require("@" in target, f"remote action/workflow is unpinned: {target}")
        ref = target.rsplit("@", 1)[1]
        require(
            re.fullmatch(FULL_SHA, ref) is not None,
            f"remote action/workflow must use an exact commit: {target}",
        )

    checkout_count = sum("actions/checkout@" in target for target in uses)
    require(checkout_count > 0, "candidate workflow must check out reviewed inputs")
    require(
        text.count("persist-credentials: false") >= checkout_count,
        "every checkout must explicitly disable persisted credentials",
    )

    require_all(
        text,
        (
            '[[ "$ZED_CLI_REF" =~ ^[0-9a-f]{40}$ ]]',
            '[[ "$HARNESS_REF" =~ ^[0-9a-f]{40}$ ]]',
            '[[ "$ZED_INTERFACES_REF" =~ ^[0-9a-f]{40}$ ]]',
            're.fullmatch(r"[0-9a-f]{40}", ref)',
            "fixture_refs=",
            "${{ matrix.fixture.ref }}",
            "${{ env.HARNESS_REF }}",
            "${{ env.ZED_CLI_REF }}",
            "${{ env.ZED_INTERFACES_REF }}",
            "scripts/candidate_lifecycle.py",
            "--fixture-refs-json",
            "sha256sum --check SHA256SUMS",
        ),
        "candidate workflow",
    )


def audit_wrapper(text: str) -> None:
    """Audit the exact-ref dependency wrapper used by fixture jobs."""

    require_all(
        text,
        (
            're.fullmatch(r"[0-9a-f]{40}", ref)',
            "root fixture",
            "dependency fixture",
            '"fetch",',
            '"--depth",',
            '"--no-tags",',
            '"checkout", "--detach", "FETCH_HEAD"',
            '"rev-parse", "HEAD"',
        ),
        "candidate lifecycle wrapper",
    )
    require(
        '"clone",\n                "--depth"' not in text,
        "candidate dependency fetch must not clone a mutable default branch",
    )
    require("ref: main" not in text, "candidate wrapper may not contain a mutable main ref")


def audit_documentation(text: str) -> None:
    """Keep human procedure text aligned with the executable gate."""

    require_all(
        text,
        (
            "Two gates, two purposes",
            "exact 40-character `zed-cli` commit",
            "exact 40-character `zed-pkg-e2e` harness commit",
            "never follows a fixture default branch",
            "Full candidate certification",
            ".github/workflows/lifecycle.yml",
            ".github/workflows/e2e.yml",
            ".github/workflows/install-boundaries.yml",
            "same immutable candidate and dependency graph",
            "zed-pkg-test certification runbook",
            "product regression, fixture drift, harness defect, or",
        ),
        "candidate validation documentation",
    )


def audit_repository(root: Path) -> None:
    workflow = root / ".github/workflows/candidate-smoke.yml"
    wrapper = root / "scripts/candidate_lifecycle.py"
    documentation = root / "docs/candidate-validation.md"
    for path in (workflow, wrapper, documentation):
        require(path.is_file(), f"required procedure file is missing: {path.relative_to(root)}")

    audit_workflow(workflow.read_text(encoding="utf-8"))
    audit_wrapper(wrapper.read_text(encoding="utf-8"))
    audit_documentation(documentation.read_text(encoding="utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        audit_repository(args.root.resolve())
    except ContractViolation as error:
        print(f"zed-pkg-test procedure contract failed: {error}", file=sys.stderr)
        return 1
    print("zed-pkg-test candidate procedure contract passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
