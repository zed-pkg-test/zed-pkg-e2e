#!/usr/bin/env python3
"""Workspace-aware Zed manifest discovery and parsing for DEN-2997."""

from __future__ import annotations

from dataclasses import dataclass
import tomllib
from typing import Any, Mapping, Sequence

from github_inventory_core import *  # noqa: F401,F403
import github_inventory_core as _core
import github_inventory_zed_parsers as _zed

_bounded_text = _core._bounded_text
_find_toml_line = _core._find_toml_line
_utf8 = _core._utf8
_validate_repo_path = _core._validate_repo_path
_parse_requirement = _zed._parse_requirement


@dataclass(frozen=True)
class WorkspaceMemberDeclaration:
    parent_manifest_path: str
    member_dir: str
    member_manifest_path: str


def _manifest_dir(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _manifest_path(directory: str) -> str:
    return f"{directory}/.zpkg.toml" if directory else ".zpkg.toml"


def parse_zed_document(blob: SourceBlob) -> tuple[str, dict[str, Any]]:
    text = _utf8(blob)
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ParseFailure(f"invalid {blob.path}: {error}") from error
    if not isinstance(document, dict):
        raise ParseFailure(f"{blob.path} must contain a TOML table")
    return text, document


def _strict_relative_member_dir(
    parent_manifest_path: str,
    raw_member: object,
    max_field_bytes: int,
) -> str:
    if not isinstance(raw_member, str) or not raw_member:
        raise ParseFailure("workspace member paths must be non-empty strings")
    _bounded_text(raw_member, "workspace member path", max_field_bytes)
    if raw_member != raw_member.strip():
        raise ParseFailure(f"workspace member path has surrounding whitespace: {raw_member!r}")
    if raw_member.startswith(("/", "\\")) or "\\" in raw_member or "\x00" in raw_member:
        raise ParseFailure(f"unsafe workspace member path {raw_member!r}")
    parts = raw_member.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ParseFailure(f"unsafe workspace member path {raw_member!r}")
    if parts[-1] == ".zpkg.toml":
        raise ParseFailure("workspace members must name package directories, not manifest files")
    parent_dir = _manifest_dir(parent_manifest_path)
    directory = "/".join(part for part in (parent_dir, raw_member) if part)
    _validate_repo_path(directory)
    return directory


def workspace_member_declarations(
    blob: SourceBlob,
    *,
    max_field_bytes: int,
) -> tuple[WorkspaceMemberDeclaration, ...]:
    _, document = parse_zed_document(blob)
    workspace = document.get("workspace")
    if workspace is None:
        return ()
    if not isinstance(workspace, Mapping):
        raise ParseFailure(f"{blob.path} [workspace] must be a table")
    members = workspace.get("members")
    if not isinstance(members, list) or not members:
        raise ParseFailure(f"{blob.path} [workspace].members must be a non-empty array")
    declarations: list[WorkspaceMemberDeclaration] = []
    seen_dirs: set[str] = set()
    for raw_member in members:
        member_dir = _strict_relative_member_dir(
            blob.path,
            raw_member,
            max_field_bytes,
        )
        if member_dir in seen_dirs:
            raise ParseFailure(
                f"{blob.path} declares duplicate workspace member {member_dir!r}"
            )
        seen_dirs.add(member_dir)
        declarations.append(
            WorkspaceMemberDeclaration(
                parent_manifest_path=blob.path,
                member_dir=member_dir,
                member_manifest_path=_manifest_path(member_dir),
            )
        )
    return tuple(declarations)


def normalize_local_dependency_dir(
    source_manifest_path: str,
    raw_path: object,
    max_field_bytes: int,
) -> str:
    if not isinstance(raw_path, str) or not raw_path:
        raise ParseFailure("local dependency paths must be non-empty strings")
    _bounded_text(raw_path, "local dependency path", max_field_bytes)
    if raw_path != raw_path.strip():
        raise ParseFailure(f"local dependency path has surrounding whitespace: {raw_path!r}")
    if raw_path.startswith(("/", "\\")) or "\\" in raw_path or "\x00" in raw_path:
        raise ParseFailure(f"unsafe local dependency path {raw_path!r}")
    stack = [part for part in _manifest_dir(source_manifest_path).split("/") if part]
    for part in raw_path.split("/"):
        if part in {"", "."}:
            raise ParseFailure(f"unsafe local dependency path {raw_path!r}")
        if part == "..":
            if not stack:
                raise ParseFailure(f"local dependency path escapes repository: {raw_path!r}")
            stack.pop()
        else:
            stack.append(part)
    if not stack:
        raise ParseFailure(f"local dependency path resolves to repository root: {raw_path!r}")
    directory = "/".join(stack)
    _validate_repo_path(directory)
    return directory


class WorkspaceZedParsingMixin(_zed.ZedParsingMixin):
    """Adds workspace/member identity and local-path resolution to Zed parsing."""

    def _parse_zed_manifests(
        self,
        blobs: Mapping[str, SourceBlob],
        workspace_declarations: Sequence[WorkspaceMemberDeclaration],
    ) -> None:
        zed_blobs = {
            path: blob
            for path, blob in blobs.items()
            if path == ".zpkg.toml" or path.endswith("/.zpkg.toml")
        }
        if ".zpkg.toml" not in zed_blobs:
            return

        self._zed_manifest_packages: dict[str, dict[str, Any]] = {}
        self._zed_path_dependencies: list[dict[str, Any]] = []
        self._zed_workspace_enabled = bool(workspace_declarations)

        for path in sorted(zed_blobs):
            info = self._parse_workspace_zed_manifest(zed_blobs[path])
            self._zed_manifest_packages[path] = info

        root = self._zed_manifest_packages[".zpkg.toml"]
        self.package_roots[root["repository"]] = root["node_id"]

        declared_member_paths: set[str] = set()
        for declaration in workspace_declarations:
            parent = self._zed_manifest_packages.get(declaration.parent_manifest_path)
            member = self._zed_manifest_packages.get(declaration.member_manifest_path)
            if parent is None or member is None:
                raise ParseFailure(
                    "workspace declaration references an unparsed manifest: "
                    f"{declaration.parent_manifest_path} -> {declaration.member_manifest_path}"
                )
            declared_member_paths.add(declaration.member_manifest_path)
            parent_node = self.nodes[parent["node_id"]]
            parent_metadata = parent_node.setdefault("zed_workspace", {})
            parent_members = parent_metadata.setdefault("member_manifests", [])
            if declaration.member_manifest_path not in parent_members:
                parent_members.append(declaration.member_manifest_path)
                parent_members.sort()
            member_node = self.nodes[member["node_id"]]
            memberships = member_node.setdefault("zed_workspace_memberships", [])
            membership = {
                "parent_manifest": declaration.parent_manifest_path,
                "member_manifest": declaration.member_manifest_path,
                "member_dir": declaration.member_dir,
            }
            if membership not in memberships:
                memberships.append(membership)
                memberships.sort(
                    key=lambda item: (
                        item["parent_manifest"],
                        item["member_manifest"],
                    )
                )
            self._add_edge(
                parent["node_id"],
                member["node_id"],
                "zed-workspace-member",
                source_path=declaration.member_dir,
                input_name=member["name"],
                provenance=[
                    provenance(parent["blob"], _find_toml_line(parent["text"], "members")),
                    provenance(member["blob"], _find_toml_line(member["text"], "name")),
                ],
            )

        if self._zed_workspace_enabled:
            unexpected = sorted(set(self._zed_manifest_packages) - {".zpkg.toml"} - declared_member_paths)
            if unexpected:
                raise ParseFailure(
                    "workspace contains fetched but undeclared member manifests: "
                    + ", ".join(unexpected)
                )

        self._resolve_zed_path_dependencies()

    def _parse_workspace_zed_manifest(self, blob: SourceBlob) -> dict[str, Any]:
        text, document = parse_zed_document(blob)
        package = document.get("package")
        if not isinstance(package, Mapping):
            raise ParseFailure(f"{blob.path} [package] must be a table")

        repository_owner = blob.repository.split("/", 1)[0]
        raw_org = package.get("org", repository_owner)
        org = normalize_package_org(str(raw_org))
        name = normalize_package_name(str(package.get("name", "")))
        coordinate = f"{org}/{name}"
        version = package.get("version")
        exact_version = (
            normalize_exact_version(version, f"{blob.path} package.version")
            if isinstance(version, str)
            else None
        )
        package_id = package_node_id(coordinate, exact_version)
        existing = self.nodes.get(package_id)
        if existing is not None:
            previous_path = existing.get("zed_manifest_path")
            if previous_path is not None and previous_path != blob.path:
                raise ParseFailure(
                    f"ambiguous Zed package identity {package_id}: "
                    f"{previous_path} and {blob.path}"
                )
        self._ensure_package_node(coordinate, exact_version)
        node = self.nodes[package_id]
        node["zed_manifest_path"] = blob.path
        node["repository"] = blob.repository
        node["repository_commit"] = blob.repository_commit
        node["blob_sha"] = blob.blob_sha

        targets = document.get("targets")
        if targets is not None and not isinstance(targets, Mapping):
            raise ParseFailure(f"{blob.path} targets must be a table")
        if isinstance(targets, Mapping):
            runtime_targets: list[str] = []
            target_metadata: dict[str, Any] = {}
            for target_name in sorted(targets, key=str):
                raw_target = targets[target_name]
                _bounded_text(str(target_name), "Zed target name", self.limits.max_field_bytes)
                if not isinstance(raw_target, Mapping):
                    raise ParseFailure(f"Zed target {target_name!r} must be a table")
                normalized_target: dict[str, Any] = {}
                for key in ("kind", "runtime", "language"):
                    value = raw_target.get(key)
                    if isinstance(value, str):
                        _bounded_text(value, f"Zed target {target_name}.{key}", self.limits.max_field_bytes)
                        normalized_target[key] = value
                if raw_target.get("host_only") is True:
                    normalized_target["host_only"] = True
                if raw_target.get("dev_only") is True:
                    normalized_target["dev_only"] = True
                target_metadata[str(target_name)] = normalized_target
                runtime = normalized_target.get("runtime") or normalized_target.get("kind")
                if isinstance(runtime, str):
                    runtime_targets.append(runtime)
            node["zed_targets"] = target_metadata
            if runtime_targets:
                node["runtime_targets"] = sorted(set(runtime_targets))

        containers = document.get("containers")
        if containers is not None:
            if not isinstance(containers, Mapping):
                raise ParseFailure(f"{blob.path} containers must be a table")
            normalized_containers: dict[str, Any] = {}
            for container_name in sorted(containers, key=str):
                raw_container = containers[container_name]
                if not isinstance(raw_container, Mapping):
                    raise ParseFailure(f"container {container_name!r} must be a table")
                normalized_container: dict[str, Any] = {}
                for key in ("file", "context", "target", "platform"):
                    value = raw_container.get(key)
                    if isinstance(value, str):
                        _bounded_text(value, f"container {container_name}.{key}", self.limits.max_field_bytes)
                        normalized_container[key] = value
                normalized_containers[str(container_name)] = normalized_container
            node["zed_containers"] = normalized_containers

        for table_name, scope in (
            ("dependencies", "runtime"),
            ("dev-dependencies", "dev"),
            ("build-dependencies", "build"),
            ("build_dependencies", "build"),
        ):
            dependencies = document.get(table_name, {})
            if not isinstance(dependencies, Mapping):
                raise ParseFailure(f"{blob.path} {table_name} must be a table")
            for dependency_name in sorted(dependencies, key=str):
                detail = dependencies[dependency_name]
                dep_name = normalize_package_name(str(dependency_name))
                dep_org = org
                requirement: str | None = None
                source_path: str | None = None
                provenance_items = [
                    provenance(blob, _find_toml_line(text, str(dependency_name)))
                ]
                if isinstance(detail, str):
                    requirement = _parse_requirement(
                        detail,
                        f"{blob.path} dependency {dependency_name}",
                        self.limits,
                    )
                elif isinstance(detail, Mapping):
                    detail_org = detail.get("org")
                    if isinstance(detail_org, str):
                        dep_org = normalize_package_org(detail_org)
                    detail_name = detail.get("package")
                    if isinstance(detail_name, str):
                        dep_name = normalize_package_name(detail_name)
                    detail_requirement = detail.get("version")
                    if isinstance(detail_requirement, str):
                        requirement = _parse_requirement(
                            detail_requirement,
                            f"{blob.path} dependency {dependency_name}.version",
                            self.limits,
                        )
                    path_value = detail.get("path")
                    if isinstance(path_value, str):
                        source_path = path_value
                else:
                    raise ParseFailure(
                        f"{blob.path} dependency {dependency_name!r} must be a string or table"
                    )

                relationship = (
                    "zed-dev-declared"
                    if scope == "dev"
                    else "zed-build-declared"
                    if scope == "build"
                    else "zed-declared"
                )
                if source_path is not None:
                    self._zed_path_dependencies.append(
                        {
                            "source": package_id,
                            "source_manifest": blob.path,
                            "dependency_key": str(dependency_name),
                            "dependency_name": dep_name,
                            "dependency_org": dep_org,
                            "requirement": requirement,
                            "relationship": relationship,
                            "raw_path": source_path,
                            "provenance": provenance_items,
                        }
                    )
                    continue

                target_coordinate = f"{dep_org}/{dep_name}"
                target = self._ensure_package_node(target_coordinate, None)
                self._add_edge(
                    package_id,
                    target,
                    relationship,
                    requirement=requirement,
                    provenance=provenance_items,
                )

        return {
            "blob": blob,
            "text": text,
            "document": document,
            "repository": blob.repository,
            "node_id": package_id,
            "org": org,
            "name": name,
            "coordinate": coordinate,
            "version": exact_version,
            "manifest_path": blob.path,
            "directory": _manifest_dir(blob.path),
        }

    def _resolve_zed_path_dependencies(self) -> None:
        by_directory = {
            info["directory"]: info for info in self._zed_manifest_packages.values()
        }
        for dependency in self._zed_path_dependencies:
            target_dir = normalize_local_dependency_dir(
                dependency["source_manifest"],
                dependency["raw_path"],
                self.limits.max_field_bytes,
            )
            target_info = by_directory.get(target_dir)
            if target_info is None:
                if self._zed_workspace_enabled:
                    raise ParseFailure(
                        f"workspace dependency {dependency['source_manifest']}:"
                        f"{dependency['dependency_key']} targets missing member {target_dir!r}"
                    )
                target_coordinate = (
                    f"{dependency['dependency_org']}/{dependency['dependency_name']}"
                )
                target = self._ensure_package_node(target_coordinate, None)
                target_manifest_path = None
                target_provenance: list[Mapping[str, Any]] = []
            else:
                if target_info["name"] != dependency["dependency_name"]:
                    raise ParseFailure(
                        f"local dependency {dependency['dependency_key']!r} in "
                        f"{dependency['source_manifest']} resolves to package "
                        f"{target_info['name']!r}, not {dependency['dependency_name']!r}"
                    )
                if target_info["org"] != dependency["dependency_org"]:
                    raise ParseFailure(
                        f"local dependency {dependency['dependency_key']!r} in "
                        f"{dependency['source_manifest']} resolves to org "
                        f"{target_info['org']!r}, not {dependency['dependency_org']!r}"
                    )
                target = target_info["node_id"]
                target_manifest_path = target_info["manifest_path"]
                target_provenance = [
                    provenance(
                        target_info["blob"],
                        _find_toml_line(target_info["text"], "name"),
                    )
                ]
            self._add_edge(
                dependency["source"],
                target,
                dependency["relationship"],
                requirement=dependency["requirement"],
                source_path=dependency["raw_path"],
                input_name=target_manifest_path,
                provenance=[*dependency["provenance"], *target_provenance],
            )
