#!/usr/bin/env python3
"""Repository/source acquisition and typed inventory construction for DEN-2957."""

from __future__ import annotations

from github_inventory_core import *  # noqa: F401,F403 - internal reference modules share one contract
import github_inventory_core as _core
from github_inventory_graph import analyze_graph

_bounded_text = _core._bounded_text
_edge_sort_key = _core._edge_sort_key
_error_code = _core._error_code
_find_json_line = _core._find_json_line
_find_toml_line = _core._find_toml_line
_json_pointer = _core._json_pointer
_metadata_full_name = _core._metadata_full_name
_provenance_sort_key = _core._provenance_sort_key
_utf8 = _core._utf8
_validate_repo_path = _core._validate_repo_path
_walk_json = _core._walk_json

from github_inventory_parsers import InventoryParsingMixin

from github_inventory_acquisition import InventoryAcquisitionMixin
from github_inventory_mutation import InventoryMutationMixin


class InventoryBuilder(
    InventoryAcquisitionMixin, InventoryParsingMixin, InventoryMutationMixin
):
    def __init__(
        self,
        client: GitHubClient,
        limits: Limits,
        repositories: Sequence[str],
        organizations: Sequence[str],
        includes: Sequence[str],
        token: str | None,
    ) -> None:
        self.client = client
        self.limits = limits
        self.requested_repositories = tuple(sorted(set(repositories)))
        self.requested_organizations = tuple(sorted(set(organizations)))
        self.includes = tuple(sorted(set(includes)))
        self.token = token
        self.repository_records: dict[str, dict[str, Any]] = {}
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[tuple[Any, ...], dict[str, Any]] = {}
        self.failures: list[dict[str, Any]] = []
        self.contradictions: list[dict[str, Any]] = []
        self.package_roots: dict[str, str] = {}

    def build(self) -> dict[str, Any]:
        metadata = self._discover_repositories()
        if len(metadata) > self.limits.max_repositories:
            raise LimitError(
                f"repository limit exceeded ({len(metadata)} > {self.limits.max_repositories})"
            )
        for full_name in sorted(metadata):
            self.client.budget.check_time()
            self._scan_repository(full_name, metadata[full_name])
        self._check_graph_limits()
        analysis = analyze_graph(self.nodes, self.edges.values())
        inventory = {
            "schema": INVENTORY_SCHEMA,
            "completeness": {
                "inventory": "partial" if self.failures else "complete",
                "resolution": "not-claimed",
            },
            "inputs": {
                "repositories": list(self.requested_repositories),
                "organizations": list(self.requested_organizations),
                "includes": list(self.includes),
            },
            "limits": self.limits.as_dict(),
            "usage": {
                "requests": self.client.budget.requests,
                "response_bytes": self.client.budget.response_bytes,
            },
            "repositories": sorted(
                self.repository_records.values(), key=lambda item: item["full_name"]
            ),
            "nodes": sorted(self.nodes.values(), key=lambda item: item["id"]),
            "edges": sorted(self.edges.values(), key=_edge_sort_key),
            "strongly_connected_components": analysis["components"],
            "cycles": analysis["cycles"],
            "topological_waves": analysis["waves"],
            "contradictions": sorted(
                self.contradictions,
                key=lambda item: (
                    item.get("code", ""),
                    item.get("source", ""),
                    item.get("target", ""),
                    json.dumps(item, sort_keys=True, separators=(",", ":")),
                ),
            ),
            "failures": sorted(
                self.failures,
                key=lambda item: (
                    item.get("repository", ""),
                    item.get("stage", ""),
                    item.get("path", ""),
                    item.get("code", ""),
                ),
            ),
        }
        return inventory
