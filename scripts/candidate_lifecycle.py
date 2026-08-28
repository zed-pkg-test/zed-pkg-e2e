#!/usr/bin/env python3
"""Run the lifecycle harness with an exact, fail-closed fixture dependency graph."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import lifecycle


def parse_fixture_refs(raw: str) -> dict[str, str]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError(
            f"--fixture-refs-json is not valid JSON: {error}"
        ) from error
    if not isinstance(value, dict) or not value:
        raise argparse.ArgumentTypeError(
            "--fixture-refs-json must be a non-empty object of repo -> commit"
        )

    result: dict[str, str] = {}
    for repo, ref in value.items():
        if not isinstance(repo, str) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", repo):
            raise argparse.ArgumentTypeError(f"invalid fixture repository name: {repo!r}")
        if not isinstance(ref, str) or not re.fullmatch(r"[0-9a-f]{40}", ref):
            raise argparse.ArgumentTypeError(
                f"fixture ref must be an exact 40-character commit: {repo}={ref!r}"
            )
        result[repo] = ref
    return result


class PinnedHarness(lifecycle.Harness):
    """Lifecycle harness whose dependency clones can never follow a mutable branch."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.fixture_refs: dict[str, str] = args.fixture_refs_json
        super().__init__(args)
        expected_root = self.fixture_refs.get(self.repo)
        if expected_root is None:
            raise AssertionError(
                f"root fixture {self.repo!r} is absent from --fixture-refs-json"
            )
        actual_root = self.run(["git", "rev-parse", "HEAD"], cwd=self.fixture).strip()
        if actual_root != expected_root:
            raise AssertionError(
                f"root fixture ref mismatch for {self.repo}: "
                f"expected {expected_root}, got {actual_root}"
            )

    def source_root(self, repo: str) -> Path:
        if repo == self.repo:
            return self.fixture
        if repo in self.clones:
            return self.clones[repo]

        ref = self.fixture_refs.get(repo)
        if ref is None:
            raise AssertionError(
                f"dependency fixture {repo!r} has no exact commit in "
                "--fixture-refs-json"
            )

        destination = self.dependency_repos / repo
        self.run(["git", "init", "--quiet", destination])
        self.run(
            [
                "git",
                "-C",
                destination,
                "remote",
                "add",
                "origin",
                f"https://github.com/{lifecycle.ORG}/{repo}.git",
            ]
        )
        self.run(
            [
                "git",
                "-C",
                destination,
                "fetch",
                "--depth",
                "1",
                "--no-tags",
                "origin",
                ref,
            ]
        )
        self.run(
            ["git", "-C", destination, "checkout", "--detach", "FETCH_HEAD"]
        )
        self.run(
            [
                "git",
                "-C",
                destination,
                "submodule",
                "update",
                "--init",
                "--recursive",
                "--depth",
                "1",
            ]
        )

        actual = self.run(
            ["git", "-C", destination, "rev-parse", "HEAD"]
        ).strip()
        if actual != ref:
            raise AssertionError(
                f"dependency fixture ref mismatch for {repo}: expected {ref}, got {actual}"
            )

        self.clones[repo] = destination
        self.assert_git_clean(destination, f"pinned dependency clone {repo}")
        return destination


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run scripts/lifecycle.py while resolving every dependency fixture "
            "from an exact commit."
        )
    )
    parser.add_argument("--repo", required=True)
    parser.add_argument("--fixture-dir", type=Path, required=True)
    parser.add_argument("--zed", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument(
        "--fixture-refs-json",
        type=parse_fixture_refs,
        required=True,
        help='JSON object such as {"node-app":"<sha>","node-lib":"<sha>"}',
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    harness = PinnedHarness(args)
    try:
        if args.repo in lifecycle.NON_PACKAGE_REPOS:
            harness.run_non_package_contract()
        else:
            harness.run_package_contract()
        harness.finish()
        harness.log(f"\nPASS: {args.repo} (all fixture dependencies pinned)")
        return 0
    except Exception as error:  # noqa: BLE001 - CI boundary needs diagnostics.
        harness.log(f"\nFAIL: {args.repo}: {error}")
        raise


if __name__ == "__main__":
    sys.exit(main())
