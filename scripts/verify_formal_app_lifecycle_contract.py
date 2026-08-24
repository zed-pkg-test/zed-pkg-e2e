#!/usr/bin/env python3
"""Cross-repository checks for zed-sync's managed application lifecycle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    import tomllib
except ImportError as error:  # pragma: no cover - hosted runners use Python 3.11+
    raise SystemExit("Python 3.11 or newer is required for tomllib") from error


EXPECTED_EVENTS = {
    "start_requested",
    "start_succeeded",
    "start_failed",
    "connectivity_changed",
    "runtime_failed",
    "stop_requested",
    "stop_succeeded",
    "stop_failed",
    "reconcile_requested",
}
EXPECTED_OUTCOMES = {"applied", "stuttered", "stale", "rejected"}
EXPECTED_PHASES = {"stopped", "starting", "online", "offline", "stopping", "failed"}
EXPECTED_WITNESSES = {
    "online_reached",
    "offline_reached",
    "failed_reached",
    "stale_completion_reached",
    "rejected_transition_reached",
    "failure_reconciliation_reached",
}
STATE_KEYS = {
    "phase",
    "operation",
    "generation",
    "active_token",
    "desired_running",
    "online",
    "failure",
}


class ContractError(ValueError):
    """The pinned production tree does not implement the declared contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def load_json(path: Path) -> dict:
    require(path.is_file(), f"missing required JSON artifact: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(f"invalid JSON artifact {path}: {error}") from error


def verify(root: Path) -> None:
    root = root.resolve()
    manifest_path = root / "formal/app-lifecycle.fm.toml"
    model_path = root / "formal/app_lifecycle.qnt"
    fixture_path = root / "protocol/formal-app-lifecycle.json"
    schema_path = root / "protocol/formal-app-lifecycle.schema.json"

    require(manifest_path.is_file(), f"missing formal manifest: {manifest_path}")
    manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("schema_version") == 1, "formal manifest schema must be v1")
    require(manifest.get("project") == "zed-sync", "formal manifest project drifted")
    require(manifest.get("model") == "app-lifecycle-v1", "formal model identity drifted")
    require(manifest.get("language") == "quint", "formal model must remain Quint")
    require(manifest.get("spec") == "formal/app_lifecycle.qnt", "formal spec path drifted")
    require(manifest.get("main") == "app_lifecycle", "Quint main module drifted")
    require(
        set(manifest.get("invariants", [])) == {"app_lifecycle_safety"},
        "formal manifest must declare exactly app_lifecycle_safety",
    )
    require(
        set(manifest.get("witnesses", [])) == EXPECTED_WITNESSES,
        "formal manifest witness set is incomplete or unexpected",
    )
    require(
        manifest.get("verification", {}).get("backend") == "tlc"
        and manifest.get("verification", {}).get("exhaustive_finite_model") is True,
        "formal manifest must retain exhaustive finite TLC verification",
    )
    require(
        manifest.get("toolchain", {}).get("quint") == "0.32.0",
        "Quint verifier version must stay pinned",
    )

    require(model_path.is_file(), f"missing Quint model: {model_path}")
    model = model_path.read_text(encoding="utf-8")
    for symbol in {"app_lifecycle_safety", *EXPECTED_WITNESSES}:
        require(symbol in model, f"Quint model is missing declared symbol {symbol!r}")

    fixture = load_json(fixture_path)
    require(fixture.get("schema_version") == 1, "trace fixture schema must be v1")
    require(fixture.get("model") == "app-lifecycle-v1", "trace fixture model drifted")
    cases = fixture.get("cases")
    require(isinstance(cases, list) and len(cases) >= 6, "trace fixture needs six cases")

    events: set[str] = set()
    outcomes: set[str] = set()
    phases: set[str] = set()
    for case in cases:
        require(isinstance(case, dict), "every trace case must be an object")
        require(isinstance(case.get("name"), str) and case["name"], "trace case needs a name")
        steps = case.get("steps")
        require(isinstance(steps, list) and len(steps) >= 2, "trace case needs two steps")
        for step in steps:
            require(isinstance(step, dict), "every trace step must be an object")
            event = step.get("event")
            state = step.get("state")
            require(isinstance(event, dict), "trace step is missing its event object")
            require(isinstance(state, dict), "trace step is missing its state object")
            require(set(state) == STATE_KEYS, "trace state keys drifted from the contract")
            events.add(event.get("type"))
            outcomes.add(step.get("outcome"))
            phases.add(state.get("phase"))

    require(events == EXPECTED_EVENTS, f"event coverage drifted: {sorted(events)}")
    require(outcomes == EXPECTED_OUTCOMES, f"outcome coverage drifted: {sorted(outcomes)}")
    require(phases == EXPECTED_PHASES, f"phase coverage drifted: {sorted(phases)}")

    schema = load_json(schema_path)
    require(
        schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema",
        "trace schema must remain Draft 2020-12",
    )
    definitions = schema.get("$defs", {})
    require(
        set(definitions.get("event", {}).get("properties", {}).get("type", {}).get("enum", []))
        == EXPECTED_EVENTS,
        "JSON Schema event enum drifted from the fixture contract",
    )
    require(
        set(definitions.get("step", {}).get("properties", {}).get("outcome", {}).get("enum", []))
        == EXPECTED_OUTCOMES,
        "JSON Schema outcome enum drifted from the fixture contract",
    )
    require(
        set(definitions.get("state", {}).get("properties", {}).get("phase", {}).get("enum", []))
        == EXPECTED_PHASES,
        "JSON Schema phase enum drifted from the fixture contract",
    )

    runtime_contracts = {
        "Rust": (
            root / "src/lifecycle.rs",
            root / "tests/formal_app_lifecycle.rs",
            "formal-app-lifecycle.json",
        ),
        "JavaScript": (
            root / "sdk/src/lifecycle.mjs",
            root / "sdk/test/formal-app-lifecycle.test.mjs",
            "formal-app-lifecycle.json",
        ),
        "Dart": (
            root / "dart/zed_sync/lib/src/lifecycle.dart",
            root / "dart/zed_sync/test/formal_app_lifecycle_test.dart",
            "formal-app-lifecycle.json",
        ),
    }
    for language, (implementation, refinement, fixture_name) in runtime_contracts.items():
        require(implementation.is_file(), f"missing {language} lifecycle reducer")
        require(refinement.is_file(), f"missing {language} lifecycle refinement test")
        require(
            fixture_name in refinement.read_text(encoding="utf-8"),
            f"{language} refinement test no longer consumes the shared trace fixture",
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("zed_sync_root", type=Path)
    arguments = parser.parse_args()
    verify(arguments.zed_sync_root)
    print("formal application lifecycle cross-repository contract: OK")


if __name__ == "__main__":
    try:
        main()
    except ContractError as error:
        raise SystemExit(f"formal application lifecycle contract failed: {error}") from error
