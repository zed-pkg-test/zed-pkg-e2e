#!/usr/bin/env python3
"""Bounded node/edge mutation and diagnostics mixin for DEN-2957."""

from __future__ import annotations

from github_inventory_core import *  # noqa: F401,F403
import github_inventory_core as _core

_error_code = _core._error_code
_provenance_sort_key = _core._provenance_sort_key


class InventoryMutationMixin:
    def _ensure_repo_node(self, full_name: str, scanned: bool) -> str:
        full_name = normalize_repo(full_name)
        node_id = repo_node_id(full_name)
        existing = self.nodes.get(node_id)
        if existing is None:
            if len(self.nodes) >= self.limits.max_nodes:
                raise LimitError(
                    f"node limit exceeded ({len(self.nodes) + 1} > {self.limits.max_nodes})"
                )
            self.nodes[node_id] = {
                "id": node_id,
                "kind": "repository",
                "label": full_name,
                "repository": full_name,
                "scanned": bool(scanned),
            }
        elif scanned:
            existing["scanned"] = True
        return node_id

    def _ensure_package_node(self, coordinate: str, exact_version: str | None) -> str:
        coordinate = normalize_repo(coordinate)
        node_id = package_node_id(coordinate, exact_version)
        if node_id not in self.nodes:
            if len(self.nodes) >= self.limits.max_nodes:
                raise LimitError(
                    f"node limit exceeded ({len(self.nodes) + 1} > {self.limits.max_nodes})"
                )
            self.nodes[node_id] = {
                "id": node_id,
                "kind": "package",
                "label": f"{coordinate}@{exact_version}" if exact_version else coordinate,
                "package": coordinate,
                **({"version": exact_version} if exact_version else {}),
            }
        return node_id

    def _add_edge(
        self,
        source: str,
        target: str,
        kind: str,
        *,
        requirement: str | None = None,
        selected_version: str | None = None,
        selected_commit: str | None = None,
        artifact_sha256: str | None = None,
        source_path: str | None = None,
        input_name: str | None = None,
        provenance: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        if source not in self.nodes:
            raise ParseFailure(f"edge source {source} is missing")
        if target not in self.nodes:
            raise ParseFailure(f"edge target {target} is missing")
        key = (
            source,
            target,
            kind,
            requirement,
            selected_version,
            selected_commit,
            artifact_sha256,
            source_path,
            input_name,
        )
        normalized_provenance = [dict(item) for item in provenance]
        if key in self.edges:
            existing = self.edges[key]
            combined = {
                json.dumps(item, sort_keys=True, separators=(",", ":")): item
                for item in existing["provenance"] + normalized_provenance
            }
            existing["provenance"] = [combined[key] for key in sorted(combined)]
            return
        if len(self.edges) >= self.limits.max_edges:
            raise LimitError(
                f"edge limit exceeded ({len(self.edges) + 1} > {self.limits.max_edges})"
            )
        edge: dict[str, Any] = {
            "source": source,
            "target": target,
            "kind": kind,
            "provenance": sorted(normalized_provenance, key=_provenance_sort_key),
        }
        for name, value in (
            ("requirement", requirement),
            ("selected_version", selected_version),
            ("selected_commit", selected_commit),
            ("artifact_sha256", artifact_sha256),
            ("source_path", source_path),
            ("input_name", input_name),
        ):
            if value is not None:
                edge[name] = value
        self.edges[key] = edge

    def _failure(self, repository: str, stage: str, error: BaseException, path: str | None = None) -> None:
        code = _error_code(error)
        message = redact_text(str(error), self.token)
        item: dict[str, Any] = {
            "repository": repository,
            "stage": stage,
            "code": code,
            "message": message,
        }
        if path:
            item["path"] = path
        self.failures.append(item)

    def _source_failure(
        self,
        blob: SourceBlob,
        stage: str,
        code: str,
        error: BaseException,
    ) -> None:
        message = redact_text(str(error), self.token)
        self.failures.append(
            {
                "repository": blob.repository,
                "stage": stage,
                "path": blob.path,
                "code": code,
                "message": message,
                "repository_commit": blob.repository_commit,
                "blob_sha": blob.blob_sha,
            }
        )

    def _check_graph_limits(self) -> None:
        if len(self.nodes) > self.limits.max_nodes:
            raise LimitError(f"node limit exceeded ({len(self.nodes)} > {self.limits.max_nodes})")
        if len(self.edges) > self.limits.max_edges:
            raise LimitError(f"edge limit exceeded ({len(self.edges)} > {self.limits.max_edges})")
