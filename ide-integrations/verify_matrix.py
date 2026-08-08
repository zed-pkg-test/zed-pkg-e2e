#!/usr/bin/env python3
import json
import re
import sys
from pathlib import Path

REQUIRED = {"multi_root_discovery", "manifest_diagnostics", "lock_diagnostics", "materialization_diagnostics", "staging_recovery", "cli_validation", "argv_execution", "bounded_execution", "output_redaction", "explicit_mutation_confirmation", "versioned_inspect_adapter", "deterministic_fallback", "native_unit_tests", "retained_artifact"}
EXPECTED = {"sublimetext", "jetbrains", "vscode", "qtcreator", "xcode", "eclipse", "visual-studio"}
SHA = re.compile(r"^[0-9a-f]{40}$")

def main(path: str) -> int:
    data = json.loads(Path(path).read_text())
    assert data["schema"] == "zed-pkg/ide-parity/v1" and data["contract_version"] == 1
    assert set(data["required_core_capabilities"]) == REQUIRED
    assert set(data["integrations"]) == EXPECTED
    repositories = set()
    for name, item in data["integrations"].items():
        assert item["repository"].startswith("zed-pkg/zed-")
        assert item["repository"] not in repositories
        repositories.add(item["repository"])
        assert item["state"] in {"live", "publish-ready-candidate", "buildable-candidate", "dedicated-repo-candidate"}
        assert item["platforms"] and item["native_language"] and item["artifact"]
        if item["state"] == "dedicated-repo-candidate":
            assert SHA.fullmatch(item["candidate_sha"]), name
            assert item["pull_request"] > 0
        elif item["state"] != "live":
            assert item.get("source")
    print(f"validated {len(EXPECTED)} IDE integrations and {len(REQUIRED)} core capabilities")
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
