#!/usr/bin/env python3
import json
import sys
from pathlib import Path
REQUIRED = {"multi_root_discovery", "manifest_diagnostics", "lock_diagnostics", "materialization_diagnostics", "staging_recovery", "cli_validation", "argv_execution", "bounded_execution", "output_redaction", "explicit_mutation_confirmation", "versioned_inspect_adapter", "deterministic_fallback", "native_unit_tests", "retained_artifact"}
EXPECTED = {"sublimetext", "jetbrains", "vscode", "qtcreator", "xcode", "eclipse", "visual-studio"}
def main(path: str) -> int:
    data = json.loads(Path(path).read_text())
    assert data["schema"] == "zed-pkg/ide-parity/v1" and data["contract_version"] == 1
    assert set(data["required_core_capabilities"]) == REQUIRED
    assert set(data["integrations"]) == EXPECTED
    for item in data["integrations"].values():
        assert item["repository"].startswith("zed-pkg/zed-")
        assert item["state"] in {"live", "publish-ready-candidate", "buildable-candidate"}
        assert item["platforms"] and item["native_language"] and item["artifact"]
        if item["state"] != "live": assert item.get("source")
    print(f"validated {len(EXPECTED)} IDE integrations and {len(REQUIRED)} core capabilities")
    return 0
if __name__ == "__main__": raise SystemExit(main(sys.argv[1]))
