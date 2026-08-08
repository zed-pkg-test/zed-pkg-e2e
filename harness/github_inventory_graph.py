#!/usr/bin/env python3
"""Deterministic SCC analysis, renderers, and atomic publication for DEN-2957."""

from __future__ import annotations

from github_inventory_core import *  # noqa: F401,F403 - internal reference modules share one contract
import github_inventory_core as _core

_fsync_directory = _core._fsync_directory

def analyze_graph(
    nodes: Mapping[str, Mapping[str, Any]],
    edges: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return deterministic SCCs and dependency-first condensation waves.

    Kosaraju's algorithm is implemented iteratively so adversarial chains up to
    the configured graph limit cannot exhaust Python's call stack.
    """

    adjacency: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    reverse: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    self_loops: set[str] = set()
    for edge in edges:
        source = str(edge["source"])
        target = str(edge["target"])
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set())
        reverse.setdefault(target, set()).add(source)
        reverse.setdefault(source, set())
        if source == target:
            self_loops.add(source)

    visited: set[str] = set()
    finish_order: list[str] = []
    for start_node in sorted(adjacency):
        if start_node in visited:
            continue
        visited.add(start_node)
        stack: list[tuple[str, Any]] = [
            (start_node, iter(sorted(adjacency[start_node])))
        ]
        while stack:
            node, iterator = stack[-1]
            try:
                target = next(iterator)
            except StopIteration:
                finish_order.append(node)
                stack.pop()
                continue
            if target in visited:
                continue
            visited.add(target)
            stack.append((target, iter(sorted(adjacency[target]))))

    assigned: set[str] = set()
    raw_components: list[tuple[str, ...]] = []
    for start_node in reversed(finish_order):
        if start_node in assigned:
            continue
        assigned.add(start_node)
        stack = [start_node]
        members: list[str] = []
        while stack:
            node = stack.pop()
            members.append(node)
            for predecessor in reversed(sorted(reverse[node])):
                if predecessor in assigned:
                    continue
                assigned.add(predecessor)
                stack.append(predecessor)
        raw_components.append(tuple(sorted(members)))

    ordered_components = sorted(raw_components)
    component_id: dict[str, str] = {}
    components: list[dict[str, Any]] = []
    for number, members in enumerate(ordered_components):
        identifier = f"scc-{number:04d}"
        cyclic = len(members) > 1 or any(member in self_loops for member in members)
        components.append({"id": identifier, "nodes": list(members), "cyclic": cyclic})
        for member in members:
            component_id[member] = identifier

    dependencies: dict[str, set[str]] = {item["id"]: set() for item in components}
    dependents: dict[str, set[str]] = {item["id"]: set() for item in components}
    for source, targets in adjacency.items():
        source_component = component_id[source]
        for target in targets:
            target_component = component_id[target]
            if source_component == target_component:
                continue
            dependencies[source_component].add(target_component)
            dependents[target_component].add(source_component)

    unresolved_count = {
        component: len(required) for component, required in dependencies.items()
    }
    current_wave = sorted(
        component for component, count in unresolved_count.items() if count == 0
    )
    waves: list[list[str]] = []
    emitted: set[str] = set()
    while current_wave:
        waves.append(current_wave)
        emitted.update(current_wave)
        next_wave: set[str] = set()
        for resolved in current_wave:
            for dependent in sorted(dependents[resolved]):
                unresolved_count[dependent] -= 1
                if unresolved_count[dependent] == 0:
                    next_wave.add(dependent)
        current_wave = sorted(next_wave)

    if len(emitted) != len(components):
        raise InventoryError("SCC condensation graph unexpectedly contains a cycle")

    cycles = [item["nodes"] for item in components if item["cyclic"]]
    return {"components": components, "cycles": cycles, "waves": waves}


def render_inventory(inventory: Mapping[str, Any], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(inventory, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    if output_format == "dot":
        return render_dot(inventory)
    if output_format == "mermaid":
        return render_mermaid(inventory)
    raise InputError(f"unsupported output format {output_format!r}")


def render_dot(inventory: Mapping[str, Any]) -> str:
    lines = [
        "digraph zpkg_github_dependency_inventory {",
        '  graph [rankdir="LR"];',
        '  node [shape="box"];',
    ]
    for node in inventory.get("nodes", []):
        identifier = render_node_id(str(node["id"]))
        label = dot_escape(str(node.get("label", node["id"])))
        kind = dot_escape(str(node.get("kind", "node")))
        lines.append(f'  {identifier} [label="{label}", tooltip="{kind}"];')
    for edge in inventory.get("edges", []):
        source = render_node_id(str(edge["source"]))
        target = render_node_id(str(edge["target"]))
        label_parts = [str(edge.get("kind", "dependency"))]
        for key in ("requirement", "selected_version", "selected_commit"):
            if edge.get(key) is not None:
                label_parts.append(str(edge[key]))
        label = dot_escape(" | ".join(label_parts))
        lines.append(f'  {source} -> {target} [label="{label}"];')
    lines.append("}")
    return "\n".join(lines) + "\n"


def render_mermaid(inventory: Mapping[str, Any]) -> str:
    lines = ["flowchart LR"]
    for node in inventory.get("nodes", []):
        identifier = render_node_id(str(node["id"]))
        label = mermaid_escape(str(node.get("label", node["id"])))
        lines.append(f'  {identifier}["{label}"]')
    for edge in inventory.get("edges", []):
        source = render_node_id(str(edge["source"]))
        target = render_node_id(str(edge["target"]))
        label_parts = [str(edge.get("kind", "dependency"))]
        for key in ("requirement", "selected_version", "selected_commit"):
            if edge.get(key) is not None:
                label_parts.append(str(edge[key]))
        label = mermaid_escape(" | ".join(label_parts))
        lines.append(f'  {source} -->|"{label}"| {target}')
    return "\n".join(lines) + "\n"


def write_atomic(path: Path, content: str) -> None:
    path = path.expanduser()
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not path.is_file():
        raise InputError(f"output path {path} is not a regular file")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        _fsync_directory(parent)
    except BaseException:
        try:
            temporary_path.unlink(missing_ok=True)
        finally:
            raise
