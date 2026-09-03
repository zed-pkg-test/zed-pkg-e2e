#!/usr/bin/env python3
"""Repository discovery and immutable workspace-aware source acquisition."""

from __future__ import annotations

from github_inventory_core import *  # noqa: F401,F403
import github_inventory_core as _core
from github_inventory_zed_workspace import (
    WorkspaceMemberDeclaration,
    workspace_member_declarations,
)

_bounded_text = _core._bounded_text
_error_code = _core._error_code
_metadata_full_name = _core._metadata_full_name
_validate_repo_path = _core._validate_repo_path


class InventoryAcquisitionMixin:
    """Acquire exact GitHub sources without changing the historical builder API."""

    def _discover_repositories(self) -> dict[str, dict[str, Any]]:
        metadata: dict[str, dict[str, Any]] = {}
        for org in self.requested_organizations:
            try:
                listed = self.client.list_org_repositories(org)
            except LimitError:
                raise
            except InventoryError as error:
                self._failure(org, "org-pagination", error)
                continue
            for item in listed:
                try:
                    full_name = _metadata_full_name(item)
                except InventoryError as error:
                    self._failure(org, "org-pagination", error)
                    continue
                metadata[full_name] = item
                if len(metadata) > self.limits.max_repositories:
                    raise LimitError(
                        "repository limit exceeded during organization pagination "
                        f"({len(metadata)} > {self.limits.max_repositories})"
                    )
        for full_name in self.requested_repositories:
            try:
                metadata[full_name] = self.client.get_repository(full_name)
            except LimitError:
                raise
            except InventoryError as error:
                self._failure(full_name, "repository-metadata", error)
                self.repository_records[full_name] = {
                    "full_name": full_name,
                    "status": "failed",
                    "archived": None,
                    "private": None,
                    "disabled": None,
                    "default_branch": None,
                    "commit_sha": None,
                    "manifests": [],
                    "missing_manifests": self._expected_manifest_paths(),
                }
        return metadata

    def _read_manifest_blob(
        self,
        *,
        full_name: str,
        commit_sha: str,
        path: str,
        entry: Mapping[str, Any],
    ) -> SourceBlob:
        _validate_repo_path(path)
        if entry.get("type") != "blob":
            raise ParseFailure(f"manifest path {path} is not a blob")
        declared_size = entry.get("size")
        if declared_size is not None:
            if not isinstance(declared_size, int) or declared_size < 0:
                raise ParseFailure(f"tree entry {path} has invalid size")
            if declared_size > self.limits.max_manifest_bytes:
                raise LimitError(
                    f"manifest {full_name}:{path} exceeded "
                    f"{self.limits.max_manifest_bytes} bytes"
                )
        blob_sha = str(entry.get("sha", "")).lower()
        if not GIT_SHA_RE.fullmatch(blob_sha):
            raise ParseFailure(f"tree entry {path} has invalid blob SHA")
        data = self.client.get_blob(full_name, blob_sha, declared_size)
        return SourceBlob(
            repository=full_name,
            repository_commit=commit_sha,
            kind=manifest_kind(path),
            path=path,
            blob_sha=blob_sha,
            data=data,
        )

    def _expand_zed_workspace_manifests(
        self,
        *,
        full_name: str,
        commit_sha: str,
        entries: Mapping[str, Mapping[str, Any]],
        blobs: dict[str, SourceBlob],
    ) -> tuple[WorkspaceMemberDeclaration, ...]:
        root = blobs.get(".zpkg.toml")
        if root is None:
            return ()

        queue: list[tuple[str, int]] = [(root.path, 0)]
        scanned: set[str] = set()
        declared_by: dict[str, str] = {}
        declarations: list[WorkspaceMemberDeclaration] = []

        while queue:
            manifest_path, depth = queue.pop(0)
            if manifest_path in scanned:
                continue
            scanned.add(manifest_path)
            if depth > self.limits.max_json_depth:
                raise LimitError(
                    "Zed workspace nesting exceeded "
                    f"{self.limits.max_json_depth} levels"
                )
            blob = blobs[manifest_path]
            for declaration in workspace_member_declarations(
                blob,
                max_field_bytes=self.limits.max_field_bytes,
            ):
                previous_parent = declared_by.get(declaration.member_manifest_path)
                if previous_parent is not None:
                    raise ParseFailure(
                        "ambiguous workspace member manifest "
                        f"{declaration.member_manifest_path!r} is declared by "
                        f"{previous_parent!r} and {declaration.parent_manifest_path!r}"
                    )
                declared_by[declaration.member_manifest_path] = (
                    declaration.parent_manifest_path
                )
                declarations.append(declaration)
                if len(declarations) > self.limits.max_tree_entries:
                    raise LimitError(
                        "Zed workspace member limit exceeded "
                        f"({len(declarations)} > {self.limits.max_tree_entries})"
                    )
                entry = entries.get(declaration.member_manifest_path)
                if entry is None:
                    raise ParseFailure(
                        f"workspace member {declaration.member_dir!r} is missing "
                        f"{declaration.member_manifest_path} at commit {commit_sha}"
                    )
                if declaration.member_manifest_path not in blobs:
                    blobs[declaration.member_manifest_path] = self._read_manifest_blob(
                        full_name=full_name,
                        commit_sha=commit_sha,
                        path=declaration.member_manifest_path,
                        entry=entry,
                    )
                queue.append((declaration.member_manifest_path, depth + 1))

        return tuple(declarations)

    def _scan_repository(self, full_name: str, metadata: Mapping[str, Any]) -> None:
        try:
            canonical = _metadata_full_name(metadata)
            if canonical != full_name:
                raise ParseFailure("GitHub metadata full_name changed during scan")
            default_branch = metadata.get("default_branch")
            if not isinstance(default_branch, str) or not default_branch:
                raise ParseFailure("repository metadata is missing default_branch")
            _bounded_text(default_branch, "default branch", self.limits.max_field_bytes)
            record: dict[str, Any] = {
                "full_name": full_name,
                "status": "scanning",
                "archived": bool(metadata.get("archived", False)),
                "private": bool(metadata.get("private", False)),
                "disabled": bool(metadata.get("disabled", False)),
                "default_branch": default_branch,
                "commit_sha": None,
                "manifests": [],
                "missing_manifests": [],
            }
            self.repository_records[full_name] = record
            self._ensure_repo_node(full_name, scanned=True)
            commit_sha = self.client.pin_default_branch(full_name, default_branch)
            record["commit_sha"] = commit_sha
            tree = self.client.get_tree(full_name, commit_sha)
            entries = self._tree_entries(tree)
            selected_paths = self._selected_manifest_paths(entries)
            record["missing_manifests"] = sorted(
                path for path in self._expected_manifest_paths() if path not in entries
            )

            blobs: dict[str, SourceBlob] = {}
            for path in sorted(selected_paths):
                blobs[path] = self._read_manifest_blob(
                    full_name=full_name,
                    commit_sha=commit_sha,
                    path=path,
                    entry=entries[path],
                )

            workspace_declarations: tuple[WorkspaceMemberDeclaration, ...] = ()
            if "zed" in self.includes:
                workspace_declarations = self._expand_zed_workspace_manifests(
                    full_name=full_name,
                    commit_sha=commit_sha,
                    entries=entries,
                    blobs=blobs,
                )

            for path in sorted(blobs):
                blob = blobs[path]
                record["manifests"].append(
                    {
                        "kind": blob.kind,
                        "path": blob.path,
                        "blob_sha": blob.blob_sha,
                        "bytes": len(blob.data),
                    }
                )
            record["manifests"].sort(key=lambda item: (item["kind"], item["path"]))
            if workspace_declarations:
                record["zed_workspace_members"] = [
                    {
                        "parent_manifest": item.parent_manifest_path,
                        "member_dir": item.member_dir,
                        "member_manifest": item.member_manifest_path,
                    }
                    for item in workspace_declarations
                ]

            if ".zpkg.toml" in blobs:
                if workspace_declarations:
                    self._parse_zed_manifests(blobs, workspace_declarations)
                else:
                    self._parse_zed_manifest(blobs[".zpkg.toml"])
            if ".zpkg.lock" in blobs:
                self._parse_zed_lock(blobs[".zpkg.lock"])
            gitmodules = blobs.get(".gitmodules")
            gitlinks = self._exact_gitlinks(entries)
            if "git-submodule" in self.includes:
                self._parse_git_submodules(gitmodules, full_name, commit_sha, gitlinks)
            if "nix" in self.includes:
                if "flake.lock" in blobs:
                    self._parse_flake_lock(blobs["flake.lock"])
                if "flake.nix" in blobs:
                    self._parse_flake_nix(blobs["flake.nix"])
                for path in sorted(blobs):
                    if path.endswith("sources.json") and path != "flake.lock":
                        self._parse_nix_sources(blobs[path])
            record["status"] = "scanned"
        except LimitError:
            raise
        except InventoryError as error:
            self._failure(full_name, "repository-scan", error)
            record = self.repository_records.setdefault(
                full_name,
                {
                    "full_name": full_name,
                    "archived": bool(metadata.get("archived", False)),
                    "private": bool(metadata.get("private", False)),
                    "disabled": bool(metadata.get("disabled", False)),
                    "default_branch": metadata.get("default_branch"),
                    "commit_sha": None,
                    "manifests": [],
                    "missing_manifests": self._expected_manifest_paths(),
                },
            )
            record["status"] = "failed"

    def _tree_entries(
        self, tree: Sequence[Mapping[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for entry in tree:
            path = entry.get("path")
            if not isinstance(path, str):
                raise ParseFailure("tree entry path is missing")
            _validate_repo_path(path)
            if path in result:
                raise ParseFailure(f"duplicate tree path {path}")
            result[path] = dict(entry)
        return result

    def _exact_gitlinks(
        self, entries: Mapping[str, Mapping[str, Any]]
    ) -> dict[str, str]:
        gitlinks: dict[str, str] = {}
        for path, entry in entries.items():
            type_is_commit = entry.get("type") == "commit"
            mode_is_gitlink = str(entry.get("mode", "")) == "160000"
            if type_is_commit != mode_is_gitlink:
                raise ParseFailure(
                    f"tree entry {path} has contradictory gitlink type/mode"
                )
            if not type_is_commit:
                continue
            object_id = str(entry.get("sha", "")).lower()
            if not GIT_SHA_RE.fullmatch(object_id):
                raise ParseFailure(f"gitlink tree entry {path} has invalid object ID")
            gitlinks[path] = object_id
        return gitlinks

    def _selected_manifest_paths(
        self, entries: Mapping[str, Mapping[str, Any]]
    ) -> set[str]:
        selected: set[str] = set()
        for include in self.includes:
            for path in EXPECTED_MANIFESTS[include]:
                entry = entries.get(path)
                if entry and entry.get("type") == "blob":
                    selected.add(path)
        if "nix" in self.includes:
            for path, entry in entries.items():
                if entry.get("type") != "blob":
                    continue
                if (
                    path.startswith("nix/") or path.startswith("npins/")
                ) and path.endswith("sources.json"):
                    selected.add(path)
        return selected

    def _expected_manifest_paths(self) -> list[str]:
        paths: set[str] = set()
        for include in self.includes:
            paths.update(EXPECTED_MANIFESTS[include])
        return sorted(paths)
