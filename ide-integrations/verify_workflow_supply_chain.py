#!/usr/bin/env python3
import json
import re
import sys
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{40}$")
USES = re.compile(r"(?m)^\s*-?\s*uses:\s*([^\s#]+)")
REPO_REF = re.compile(
    r"repository:\s*(zed-pkg/zed-(?:vscode|qtcreator|xcode|eclipse|visual-studio))\s*\n\s*ref:\s*([0-9a-f]{40})"
)


def main(workflow_path: str, matrix_path: str) -> int:
    workflow = Path(workflow_path).read_text()
    matrix = json.loads(Path(matrix_path).read_text())
    errors = []

    for uses in USES.findall(workflow):
        if uses.startswith("./"):
            continue
        if "@" not in uses:
            errors.append(f"action has no immutable ref: {uses}")
            continue
        ref = uses.rsplit("@", 1)[1]
        if not SHA.fullmatch(ref):
            errors.append(f"action ref is not a full commit SHA: {uses}")

    for forbidden in ("ubuntu-latest", "macos-latest", "windows-latest"):
        if forbidden in workflow:
            errors.append(f"mutable runner label is forbidden: {forbidden}")

    for forbidden in ("npm install ", "npx --yes", "persist-credentials: true"):
        if forbidden in workflow:
            errors.append(f"forbidden mutable/credential-bearing workflow fragment: {forbidden}")

    expected = {
        item["repository"]: item["candidate_sha"]
        for item in matrix["integrations"].values()
        if item.get("state") == "dedicated-repo-candidate"
    }
    observed = dict(REPO_REF.findall(workflow))
    if observed != expected:
        errors.append(f"workflow candidate refs differ from parity matrix: observed={observed!r} expected={expected!r}")

    if workflow.count("persist-credentials: false") < 8:
        errors.append("every checkout path should explicitly disable persisted credentials")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        f"verified {len(USES.findall(workflow))} immutable action uses, "
        f"{len(expected)} exact candidate refs, fixed runners, npm-ci discipline, and no persisted credentials"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
