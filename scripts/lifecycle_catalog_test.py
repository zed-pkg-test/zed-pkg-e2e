#!/usr/bin/env python3
"""Fail-fast checks for lifecycle fixture classification and dependency mappings."""

from __future__ import annotations

import argparse
import importlib.util
import sys
import tomllib
from pathlib import Path
from types import ModuleType


def load_lifecycle(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("zed_lifecycle", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load lifecycle harness: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_manifest(root: Path) -> dict:
    with (root / ".zpkg.toml").open("rb") as handle:
        return tomllib.load(handle)


def package_name(manifest: dict) -> str:
    package = manifest["package"]
    return f"{package['org']}/{package['name']}"


def assert_fixture_classification(
    lifecycle: ModuleType, repo: str, root: Path
) -> dict | None:
    manifest_path = root / ".zpkg.toml"
    declared_non_package = repo in lifecycle.NON_PACKAGE_REPOS
    if manifest_path.is_file():
        if declared_non_package:
            raise AssertionError(
                f"{repo} has .zpkg.toml but is still listed in NON_PACKAGE_REPOS"
            )
        return read_manifest(root)
    if not declared_non_package:
        raise AssertionError(
            f"{repo} has no .zpkg.toml and must be explicitly classified as non-package"
        )
    return None


def assert_dependencies_are_mapped(lifecycle: ModuleType, repo: str, manifest: dict) -> None:
    dependencies = lifecycle.manifest_dependencies(manifest)
    missing = sorted(
        dependency
        for dependency in dependencies
        if dependency not in lifecycle.PACKAGE_SOURCES
    )
    if missing:
        raise AssertionError(
            f"{repo} has lifecycle dependencies without PACKAGE_SOURCES entries: {missing}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness-root", type=Path, required=True)
    parser.add_argument("--shared-schema", type=Path, required=True)
    parser.add_argument("--python-app", type=Path, required=True)
    parser.add_argument("--polyglot-node-app", type=Path, required=True)
    args = parser.parse_args()

    harness_root = args.harness_root.resolve()
    lifecycle = load_lifecycle(harness_root / "scripts" / "lifecycle.py")

    harness_manifest = assert_fixture_classification(
        lifecycle, "zed-pkg-e2e", harness_root
    )
    if harness_manifest is not None:
        raise AssertionError("zed-pkg-e2e unexpectedly became a package fixture")

    shared_manifest = assert_fixture_classification(
        lifecycle, "shared-schema", args.shared_schema.resolve()
    )
    if shared_manifest is None:
        raise AssertionError("shared-schema must be a package fixture")
    shared_package = package_name(shared_manifest)
    expected_mapping = ("shared-schema", ".")
    actual_mapping = lifecycle.PACKAGE_SOURCES.get(shared_package)
    if actual_mapping != expected_mapping:
        raise AssertionError(
            f"{shared_package} source mapping is {actual_mapping!r}, expected {expected_mapping!r}"
        )

    for repo, root in (
        ("python-app", args.python_app.resolve()),
        ("polyglot-node-app", args.polyglot_node_app.resolve()),
    ):
        manifest = assert_fixture_classification(lifecycle, repo, root)
        if manifest is None:
            raise AssertionError(f"{repo} must be a package fixture")
        assert_dependencies_are_mapped(lifecycle, repo, manifest)

    source_repos = {repo for repo, _relative in lifecycle.PACKAGE_SOURCES.values()}
    overlap = sorted(source_repos & lifecycle.NON_PACKAGE_REPOS)
    if overlap:
        raise AssertionError(
            f"package source repositories are classified as non-package: {overlap}"
        )

    print(
        "lifecycle catalog OK: shared-schema is a package, dependent sources are mapped, "
        "and non-package classifications are disjoint"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
