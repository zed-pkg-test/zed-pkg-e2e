#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <zed-binary> <fresh-work-root>" >&2
  exit 64
fi

zed_bin=$(cd "$(dirname "$1")" && pwd -P)/$(basename "$1")
work_root=$2

if [[ ! -x "$zed_bin" ]]; then
  echo "zed binary is not executable: $zed_bin" >&2
  exit 66
fi
if [[ -e "$work_root" ]]; then
  echo "work root must not already exist: $work_root" >&2
  exit 73
fi

mkdir -p "$work_root"/{sources,home,project}
for node in a-1 b-1 a-2 b-0; do
  mkdir -p "$work_root/sources/$node"
  printf '%s\n' "$node" >"$work_root/sources/$node/payload.txt"
done

python3 - "$work_root" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1]).resolve()
registry = "fixture-registry"
org = "fixture"


def identity(name: str, version: str) -> dict[str, str]:
    return {
        "registry_id": registry,
        "org": org,
        "name": name,
        "version": version,
    }


def node(name: str, version: str, seed: str) -> dict[str, object]:
    return {
        "id": identity(name, version),
        "artifact_digest": f"sha256:{seed * 64}",
        "features": [],
    }


def edge(
    from_name: str,
    from_version: str,
    to_name: str,
    to_version: str,
) -> dict[str, object]:
    return {
        "from": identity(from_name, from_version),
        "to": identity(to_name, to_version),
        "kind": "runtime",
        "requirement": f"={to_version}",
        "target": None,
        "optional": False,
        "features": [],
    }


a1 = identity("a", "1")
b1 = identity("b", "1")
a2 = identity("a", "2")
b0 = identity("b", "0")
graph = {
    "schema": "zpkg/dependency-graph/v1",
    "view": "resolved",
    "completeness": "complete",
    "roots": [a1],
    "nodes": [
        node("a", "1", "1"),
        node("b", "1", "2"),
        node("a", "2", "3"),
        node("b", "0", "4"),
    ],
    "edges": [
        edge("a", "1", "b", "1"),
        edge("b", "1", "a", "2"),
        edge("a", "2", "b", "0"),
        edge("b", "0", "a", "2"),
    ],
    "provenance": {
        "resolver_version": "zed-pkg-test/den-3488",
        "target": "host",
        "enabled_features": [],
        "registry_snapshots": [
            {
                "registry_id": registry,
                "checkpoint_digest": f"sha256:{'e' * 64}",
            }
        ],
        "lock_digest": f"sha256:{'d' * 64}",
    },
    "parent_graph_digest": None,
    "projection": None,
    "graph_digest": None,
}
plan = {
    "schema": "zpkg/local-graph-materialization/v1",
    "graph": graph,
    "sources": [
        {"id": a1, "source": str(root / "sources" / "a-1")},
        {"id": b1, "source": str(root / "sources" / "b-1")},
        {"id": a2, "source": str(root / "sources" / "a-2")},
        {"id": b0, "source": str(root / "sources" / "b-0")},
    ],
}
(root / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
PY

run_materialize() {
  local output=$1
  "$zed_bin" \
    --home "$work_root/home" \
    graph materialize \
    --plan "$work_root/plan.json" \
    --project "$work_root/project" \
    2>&1 | tee "$output"
}

run_materialize "$work_root/first.log"

grep -Fq 'circular dependency detected:' "$work_root/first.log"
grep -Fq 'fixture-registry::fixture/a@2' "$work_root/first.log"
grep -Fq 'fixture-registry::fixture/b@0' "$work_root/first.log"
grep -Fq 'reuses the existing exact node through a symlink' "$work_root/first.log"

python3 - "$work_root" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1]).resolve()


def report(path: pathlib.Path) -> dict[str, object]:
    candidates = [line for line in path.read_text().splitlines() if line.startswith("{")]
    if not candidates:
        raise SystemExit(f"no JSON report found in {path}")
    return json.loads(candidates[-1])


first = report(root / "first.log")
assert first["nodes"] == 4, first
assert first["edges"] == 4, first
assert len(first["cycles"]) == 1, first
assert first["reused_generation"] is False, first

generation = pathlib.Path(first["generation_dir"])
node_dirs = sorted((generation / "nodes").iterdir())
assert len(node_dirs) == 4, node_dirs
for node_dir in node_dirs:
    payload = node_dir / "root" / "payload.txt"
    assert payload.is_symlink(), payload

project_a1 = root / "project" / "zed_modules" / "fixture" / "a"
b1 = project_a1 / "zed_modules" / "fixture" / "b"
a2 = b1 / "zed_modules" / "fixture" / "a"
b0 = a2 / "zed_modules" / "fixture" / "b"
closing_a2 = b0 / "zed_modules" / "fixture" / "a"
for link in (project_a1, b1, a2, b0, closing_a2):
    assert link.is_symlink(), link
assert closing_a2.readlink() == a2.resolve(), (closing_a2.readlink(), a2.resolve())

node_labels = {
    json.loads((node_dir / "node.json").read_text())["id"]["name"]
    + "@"
    + json.loads((node_dir / "node.json").read_text())["id"]["version"]
    for node_dir in node_dirs
}
assert node_labels == {"a@1", "b@1", "a@2", "b@0"}, node_labels

(root / "first-report.json").write_text(json.dumps(first, indent=2) + "\n")
PY

run_materialize "$work_root/second.log"

python3 - "$work_root" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1]).resolve()
first = json.loads((root / "first-report.json").read_text())
lines = [line for line in (root / "second.log").read_text().splitlines() if line.startswith("{")]
assert lines, "second run emitted no JSON report"
second = json.loads(lines[-1])
assert second["reused_generation"] is True, second
assert second["generation_dir"] == first["generation_dir"], (first, second)
assert second["graph_digest"] == first["graph_digest"], (first, second)
assert second["materialization_digest"] == first["materialization_digest"], (first, second)
PY

if "$zed_bin" \
  --home "$work_root/home" \
  graph materialize \
  --plan "$work_root/plan.json" \
  --project "$work_root/copy-project" \
  --mode copy \
  >"$work_root/copy.stdout" \
  2>"$work_root/copy.stderr"; then
  echo "copy mode unexpectedly accepted a circular exact-version graph" >&2
  exit 1
fi
grep -Fq \
  'copy mode cannot represent a circular exact-version dependency graph' \
  "$work_root/copy.stderr"

test ! -e "$work_root/copy-project/zed_modules"
printf 'DEN-3488 black-box cycle canary passed: 4 exact nodes, 4 symlink edges, 1 finite back-edge\n'
