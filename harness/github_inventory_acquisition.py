#!/usr/bin/env python3
"""Immutable GitHub repository selection and manifest acquisition for DEN-2957."""

from __future__ import annotations

from github_inventory_core import *  # noqa: F401,F403
import github_inventory_core as _core
from github_inventory_zed_workspace import (
    WorkspaceMemberDeclaration,
    workspace_member_declarations,
)

_metadata_full_name = _core._metadata_full_name
_quote = _core._quote
_validate_repo_path = _core._validate_repo_path


class RepositoryAcquisitionMixin:
    def _resolve_repositories(
        self,
        repositories: Sequence[str],
        organizations: Sequence[str],
    ) -> list[RepoInfo]:
        selected: dict[str, RepoInfo] = {}
        for repository in normalize_repositories(repositories):
            owner, name = repository.split("/", 1)
            metadata = self.client.get_json(
                f"/repos/{_quote(owner)}/{_quote(name)}",
                expected_type=dict,
            )
            full_name = _metadata_full_name(metadata)
            selected[full_name] = RepoInfo(
                full_name=full_name,
                private=bool(metadata.get("private", False)),
                archived=bool(metadata.get("archived", False)),
                default_branch=_bounded_text(
                    str(metadata.get("default_branch", "main")),
                    "default branch",
                    self.limits.max_field_bytes,
                ),
            )

        for organization in normalize_organizations(organizations):
            items = self.client.paginate(f"/orgs/{_quote(organization)}/repos?type=all&per_page=100")
            for metadata in items:
                full_name = _metadata_full_name(metadata)
                if not full_name.startswith(organization + "/"):
                    raise ParseFailure(
                        f"organization listing for {organization} returned {full_name}"
                    )
                selected.setdefault(
                    full_name,
                    RepoInfo(
                        full_name=full_name,
                        private=bool(metadata.get("private", False)),
                        archived=bool(metadata.get("archived", False)),
                        default_branch=_bounded_text(
                            str(metadata.get("default_branch", "main")),
                            "default branch",
                            self.limits.max_field_bytes,
                        ),
                    ),
                )
                if len(selected) > self.limits.max_repositories:
                    raise LimitError(
                        f"repository limit exceeded ({len(selected)} > {self.limits.max_repositories})"
                    )
        return [selected[key] for key in sorted(selected)]

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
        if not isinstance(declared_size, int) or declared_size < 0:
            raise ParseFailure(f"tree entry for {path} is missing a valid size")
        if declared_size > self.limits.max_manifest_bytes:
            raise LimitError(
                f"manifest {path} exceeds {self.limits.max_manifest_bytes} bytes"
            )
        blob_sha = entry.get("sha")
        if not isinstance(blob_sha, str):
            raise ParseFailure(f"manifest {path} is missing a blob SHA")
        data = self.client.get_blob(full_name, blob_sha)
        if len(data) != declared_size:
            raise ParseFailure(
                f"manifest {path} size mismatch: tree={declared_size} blob={len(data)}"
            )
        if len(data) > self.limits.max_manifest_bytes:
            raise LimitError(
                f"manifest {path} exceeds {self.limits.max_manifest_bytes} bytes"
            )
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
                declared_by[declaration.member_manifest_path] = declaration.parent_manifest_path
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

    def _scan_repository(self, repo: RepoInfo) -> None:
        full_name = repo.full_name
        repo_node = self._ensure_repo_node(full_name, scanned=True)
        record: dict[str, Any] = {
            "full_name": full_name,
            "private": repo.private,
            "archived": repo.archived,
            "default_branch": repo.default_branch,
            "status": "scanned",
            "manifests": [],
            "missing_manifests": [],
        }
        try:
            owner, name = full_name.split("/", 1)
            commit = self.client.get_json(
                f"/repos/{_quote(owner)}/{_quote(name)}/commits/{_quote(repo.default_branch)}",
                expected_type=dict,
            )
            commit_sha = normalize_commit_sha(commit.get("sha"), "repository commit")
            record["commit_sha"] = commit_sha
            self.nodes[repo_node]["commit_sha"] = commit_sha
            tree = self.client.get_json(
                f"/repos/{_quote(owner)}/{_quote(name)}/git/trees/{commit_sha}?recursive=1",
                expected_type=dict,
            )
            if tree.get("truncated") is True:
                raise ApiError(200, f"tree:{full_name}", code="github_tree_truncated")
            tree_items = tree.get("tree")
            if not isinstance(tree_items, list):
                raise ParseFailure("recursive tree response is missing tree array")
            if len(tree_items) > self.limits.max_tree_entries:
                raise LimitError(
                    f"tree entry limit exceeded ({len(tree_items)} > {self.limits.max_tree_entries})"
                )
            entries: dict[str, Mapping[str, Any]] = {}
            for entry in tree_items:
                if not isinstance(entry, dict):
                    raise ParseFailure("recursive tree entry must be an object")
                path = entry.get("path")
                if not isinstance(path, str):
                    raise ParseFailure("recursive tree entry is missing path")
                _validate_repo_path(path)
                if path in entries:
                    raise ParseFailure(f"duplicate recursive tree path {path}")
                entries[path] = entry

            requested_paths = sorted(
                {
                    path
                    for include in self.includes
                    for path in EXPECTED_MANIFESTS[include]
                }
            )
            blobs: dict[str, SourceBlob] = {}
            for path in requested_paths:
                entry = entries.get(path)
                if entry is None:
                    record["missing_manifests"].append(path)
                    continue
                blobs[path] = self._read_manifest_blob(
                    full_name=full_name,
                    commit_sha=commit_sha,
                    path=path,
                    entry=entry,
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
                        "size": len(blob.data),
                    }
                )
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
                self._parse_zed_manifests(blobs, workspace_declarations)
            if ".zpkg.lock" in blobs:
                self._parse_zed_lock(blobs[".zpkg.lock"])
            if ".gitmodules" in blobs:
                self._parse_gitmodules(blobs[".gitmodules"], full_name)
            if "flake.lock" in blobs:
                self._parse_flake_lock(blobs["flake.lock"], full_name)
            if "flake.nix" in blobs:
                self._parse_flake_nix(blobs["flake.nix"], full_name)
            if "nix/sources.json" in blobs:
                self._parse_nix_sources(blobs["nix/sources.json"], full_name)
            if "npins/sources.json" in blobs:
                self._parse_npins(blobs["npins/sources.json"], full_name)
            for path, entry in sorted(entries.items()):
                if path == "Dockerfile" or path.endswith("/Dockerfile"):
                    self._parse_container_path(full_name, path, entry, commit_sha)
            self.repo_records.append(record)
        except Exception as error:  # controlled per-repository failure
            record["status"] = "failed"
            record["failure_code"] = _error_code(error)
            record["failure_message"] = redact_text(str(error), self.token)
            self.repo_records.append(record)
            self._failure(full_name, "repository-scan", error)
