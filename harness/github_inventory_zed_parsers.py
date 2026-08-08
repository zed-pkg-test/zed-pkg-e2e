#!/usr/bin/env python3
"""Zed manifest and lock provenance parser mixin for DEN-2957."""

from __future__ import annotations

from github_inventory_core import *  # noqa: F401,F403
import github_inventory_core as _core

_bounded_text = _core._bounded_text
_find_json_line = _core._find_json_line
_find_toml_line = _core._find_toml_line
_json_pointer = _core._json_pointer
_utf8 = _core._utf8
_validate_repo_path = _core._validate_repo_path
_walk_json = _core._walk_json

class ZedParsingMixin:
    def _parse_zed_manifest(self, blob: SourceBlob) -> None:
        text = _utf8(blob)
        try:
            document = tomllib.loads(text)
        except tomllib.TOMLDecodeError as error:
            self._source_failure(blob, "parse-zed-manifest", "invalid_toml", error)
            return
        package = document.get("package")
        source_id = repo_node_id(blob.repository)
        if isinstance(package, dict):
            org = package.get("org")
            name = package.get("name")
            if isinstance(org, str) and isinstance(name, str):
                try:
                    coordinate = normalize_repo(f"{org}/{name}")
                except InputError as error:
                    self._source_failure(blob, "parse-zed-manifest", "invalid_package_coordinate", error)
                    return
                package_id = package_node_id(coordinate)
                self._ensure_package_node(coordinate, exact_version=None)
                self.package_roots[blob.repository] = package_id
                self._add_edge(
                    source_id,
                    package_id,
                    "contains-zed-package",
                    provenance=[provenance(blob, _find_toml_line(text, "name"))],
                )
                source_id = package_id
        dependencies = document.get("dependencies", {})
        if dependencies is None:
            dependencies = {}
        if not isinstance(dependencies, dict):
            self._source_failure(
                blob,
                "parse-zed-manifest",
                "invalid_dependencies_table",
                ParseFailure("[dependencies] must be a table"),
            )
            return
        for raw_coordinate, raw_requirement in sorted(dependencies.items(), key=lambda item: str(item[0])):
            try:
                coordinate = normalize_repo(str(raw_coordinate))
            except InputError as error:
                self._source_failure(blob, "parse-zed-manifest", "invalid_dependency_coordinate", error)
                continue
            if not isinstance(raw_requirement, str):
                self._source_failure(
                    blob,
                    "parse-zed-manifest",
                    "invalid_dependency_requirement",
                    ParseFailure(f"dependency {coordinate} requirement must be a string"),
                )
                continue
            try:
                _bounded_text(
                    raw_requirement,
                    f"dependency {coordinate} requirement",
                    self.limits.max_field_bytes,
                )
            except InputError as error:
                self._source_failure(
                    blob,
                    "parse-zed-manifest",
                    "dependency_requirement_too_large",
                    error,
                )
                continue
            target = package_node_id(coordinate)
            self._ensure_package_node(coordinate, exact_version=None)
            self._add_edge(
                source_id,
                target,
                "zed-declared",
                requirement=raw_requirement,
                provenance=[provenance(blob, _find_toml_line(text, str(raw_coordinate)))],
            )

    def _parse_zed_lock(self, blob: SourceBlob) -> None:
        text = _utf8(blob)
        try:
            document = tomllib.loads(text)
        except tomllib.TOMLDecodeError as error:
            self._source_failure(blob, "parse-zed-lock", "invalid_toml", error)
            return
        packages = document.get("package", [])
        if not isinstance(packages, list):
            self._source_failure(
                blob,
                "parse-zed-lock",
                "invalid_lock_packages",
                ParseFailure("lock package entries must be an array"),
            )
            return
        source = self.package_roots.get(blob.repository, repo_node_id(blob.repository))
        selected_by_coordinate: dict[str, set[tuple[str, str | None]]] = defaultdict(set)
        for index, item in enumerate(packages):
            if not isinstance(item, dict):
                self._source_failure(
                    blob,
                    "parse-zed-lock",
                    "invalid_lock_package",
                    ParseFailure(f"lock package {index} must be a table"),
                )
                continue
            org, name, version = item.get("org"), item.get("name"), item.get("version")
            if not all(isinstance(value, str) and value for value in (org, name, version)):
                self._source_failure(
                    blob,
                    "parse-zed-lock",
                    "invalid_lock_package",
                    ParseFailure(f"lock package {index} is missing org/name/version"),
                )
                continue
            try:
                coordinate = normalize_repo(f"{org}/{name}")
            except InputError as error:
                self._source_failure(blob, "parse-zed-lock", "invalid_lock_coordinate", error)
                continue
            try:
                _bounded_text(version, f"lock version for {coordinate}", self.limits.max_field_bytes)
            except InputError as error:
                self._source_failure(blob, "parse-zed-lock", "invalid_lock_version", error)
                continue
            sha256 = item.get("sha256") if isinstance(item.get("sha256"), str) else None
            if sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", sha256):
                self._source_failure(
                    blob,
                    "parse-zed-lock",
                    "invalid_lock_artifact_sha256",
                    ParseFailure(f"lock package {coordinate} has invalid sha256"),
                )
                continue
            target = package_node_id(coordinate, version)
            self._ensure_package_node(coordinate, exact_version=version)
            self._add_edge(
                source,
                target,
                "zed-lock-pin",
                selected_version=version,
                artifact_sha256=sha256,
                provenance=[provenance(blob, _find_toml_line(text, str(name)))],
            )
            selected_by_coordinate[coordinate].add((version, sha256))
        for coordinate, selections in sorted(selected_by_coordinate.items()):
            if len(selections) > 1:
                self.contradictions.append(
                    {
                        "code": "conflicting-zed-lock-pins",
                        "source": source,
                        "target": package_node_id(coordinate),
                        "selections": [
                            {"version": version, "artifact_sha256": sha}
                            for version, sha in sorted(selections)
                        ],
                    }
                )
