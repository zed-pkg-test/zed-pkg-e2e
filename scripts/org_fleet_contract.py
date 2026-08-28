#!/usr/bin/env python3
"""Run the public-fleet audit with reviewed Zed package namespace aliases.

The GitHub organization and Zed registry package organization are distinct
identities. Legacy fixtures intentionally use the short registry namespace
``zedtest`` while newer fixtures use ``zed-pkg-test``. This wrapper reads the
explicit allowlist from the reviewed inventory, preserves each manifest's
actual package organization in evidence, and rejects any unreviewed namespace.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Sequence

import org_fleet_inventory as fleet

REPORT_SCHEMA = "zed-pkg-test/public-fleet-inventory-v3"


def load_package_organizations(path: Path, github_organization: str) -> tuple[str, ...]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise fleet.FleetError(f"cannot read package organizations from {path}: {error}") from error
    if not isinstance(raw, dict):
        raise fleet.FleetError("inventory root must be an object")
    values = raw.get("package_organizations")
    if not isinstance(values, list) or not values:
        raise fleet.FleetError("package_organizations must be a non-empty array")

    organizations: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value.strip():
            raise fleet.FleetError(
                f"package_organizations[{index}] must be a non-empty string"
            )
        if value in organizations:
            raise fleet.FleetError(f"duplicate package organization: {value}")
        organizations.append(value)

    if github_organization not in organizations:
        raise fleet.FleetError(
            "package_organizations must include the GitHub organization "
            f"{github_organization!r}"
        )
    return tuple(organizations)


def audit_with_package_organizations(
    github_organization: str,
    package_organizations: Sequence[str],
    core: dict[str, fleet.CoreSpec],
    live_repositories: Sequence[fleet.LiveRepository],
    load_manifest: fleet.ManifestLoader,
) -> dict[str, Any]:
    allowed = frozenset(package_organizations)
    actual_organizations: dict[tuple[str, str], str | None] = {}

    def reviewed_loader(
        repository: fleet.LiveRepository,
        path: str,
    ) -> fleet.ManifestProbe:
        probe = load_manifest(repository, path)
        actual_organizations[(repository.name, path)] = probe.package_org
        if probe.present and probe.package_org in allowed:
            # The base auditor expects the GitHub org as its canonical namespace.
            # Normalize only for validation, then restore the actual value below.
            return replace(probe, package_org=github_organization)
        return probe

    report = fleet.audit_fleet(
        github_organization,
        core,
        live_repositories,
        reviewed_loader,
    )
    report["schema"] = REPORT_SCHEMA
    report["package_organizations"] = list(package_organizations)

    for row in report["repositories"]:
        manifest = row["manifest"]
        key = (str(row["name"]), str(manifest["path"]))
        actual = actual_organizations.get(key)
        manifest["package_org"] = actual
        manifest["package_org_allowed"] = actual in allowed if manifest["present"] else None

    return report


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("fixtures/org-repositories.json"),
        help="reviewed lifecycle and package-namespace inventory",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--token-env",
        default="GITHUB_TOKEN",
        help="environment variable containing a read-only GitHub token",
    )
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    repository_fetcher: Callable[..., tuple[fleet.LiveRepository, ...]] = fleet.fetch_public_repositories,
    manifest_loader_factory: Callable[..., fleet.ManifestLoader] = fleet.make_github_manifest_loader,
) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        github_organization, core = fleet.load_core_inventory(args.inventory)
        package_organizations = load_package_organizations(
            args.inventory, github_organization
        )
        token = os.environ.get(args.token_env) or None
        live = repository_fetcher(github_organization, token=token)
        loader = manifest_loader_factory(github_organization, token=token)
        report = audit_with_package_organizations(
            github_organization,
            package_organizations,
            core,
            live,
            loader,
        )
    except fleet.FleetError as error:
        print(f"fleet contract failed: {error}", file=sys.stderr)
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if report["errors"]:
        print("fleet contract failed:", file=sys.stderr)
        for error in report["errors"]:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(
        "fleet contract passed: "
        f"{report['live_repository_count']} public repositories, "
        f"{report['manifest_present_count']} valid manifests, "
        f"package organizations={','.join(report['package_organizations'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
