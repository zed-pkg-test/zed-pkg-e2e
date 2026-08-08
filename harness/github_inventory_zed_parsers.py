#!/usr/bin/env python3
"""Zed manifest and lock provenance parser mixin for DEN-2957 and DEN-2996."""

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
        """Record exact selections without inventing dependency topology.

        A flat lock closure proves selected package/version/artifact identities,
        but not parent-child relationships among those selections. Pins therefore
        live in a dedicated evidence collection. Only a proven direct declaration
        edge may be annotated with a matching unique pin.
        """

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
        selected_by_coordinate: dict[str, list[dict[str, Any]]] = defaultdict(list)
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

            sha256 = item.get("sha256")
            if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
                self._source_failure(
                    blob,
                    "parse-zed-lock",
                    "invalid_lock_artifact_sha256",
                    ParseFailure(f"lock package {coordinate} has invalid sha256"),
                )
                continue

            vcs_commit = item.get("vcs_commit")
            if vcs_commit is not None:
                if not isinstance(vcs_commit, str) or not GIT_SHA_RE.fullmatch(vcs_commit.lower()):
                    self._source_failure(
                        blob,
                        "parse-zed-lock",
                        "invalid_lock_vcs_commit",
                        ParseFailure(f"lock package {coordinate} has invalid vcs_commit"),
                    )
                    continue
                vcs_commit = vcs_commit.lower()

            pin: dict[str, Any] = {
                "kind": "zed-lock-pin",
                "topological": False,
                "repository": blob.repository,
                "source": source,
                "package": coordinate,
                "selected_version": version,
                "artifact_sha256": sha256,
                "provenance": [provenance(blob, _find_toml_line(text, str(name)))],
            }
            if vcs_commit is not None:
                pin["vcs_commit"] = vcs_commit

            pin_key = (
                blob.repository,
                source,
                coordinate,
                version,
                sha256,
                vcs_commit,
                blob.repository_commit,
                blob.blob_sha,
            )
            if pin_key not in self._pin_keys:
                if len(self.pins) >= self.limits.max_edges:
                    raise LimitError(
                        f"pin evidence limit exceeded ({len(self.pins) + 1} > {self.limits.max_edges})"
                    )
                self._pin_keys.add(pin_key)
                self.pins.append(pin)
            selected_by_coordinate[coordinate].append(pin)

        for coordinate, pins in sorted(selected_by_coordinate.items()):
            selections = {
                (
                    str(pin["selected_version"]),
                    str(pin["artifact_sha256"]),
                    pin.get("vcs_commit"),
                )
                for pin in pins
            }
            if len(selections) > 1:
                rendered_selections: list[dict[str, Any]] = []
                for version, artifact_sha256, vcs_commit in sorted(
                    selections,
                    key=lambda value: (value[0], value[1], value[2] or ""),
                ):
                    selection: dict[str, Any] = {
                        "version": version,
                        "artifact_sha256": artifact_sha256,
                    }
                    if vcs_commit is not None:
                        selection["vcs_commit"] = vcs_commit
                    rendered_selections.append(selection)
                self.contradictions.append(
                    {
                        "code": "conflicting-zed-lock-pins",
                        "source": source,
                        "target": package_node_id(coordinate),
                        "selections": rendered_selections,
                    }
                )
                continue

            selected_version, artifact_sha256, vcs_commit = next(iter(selections))
            target = package_node_id(coordinate)
            matching_pin = min(
                pins,
                key=lambda pin: json.dumps(pin, sort_keys=True, separators=(",", ":")),
            )
            for edge in self.edges.values():
                if (
                    edge.get("source") == source
                    and edge.get("target") == target
                    and edge.get("kind") == "zed-declared"
                ):
                    edge["selected_version"] = selected_version
                    edge["artifact_sha256"] = artifact_sha256
                    if vcs_commit is not None:
                        edge["selected_commit"] = vcs_commit
                    edge["selection_provenance"] = list(matching_pin["provenance"])
