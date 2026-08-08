#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence


SENSITIVE_ENV_FRAGMENTS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PRIVATE_KEY",
    "ACCESS_KEY",
    "API_KEY",
    "AUTHORIZATION",
    "COOKIE",
)


class ContractError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def sanitized_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for key in list(environment):
        upper = key.upper()
        if any(fragment in upper for fragment in SENSITIVE_ENV_FRAGMENTS):
            environment.pop(key, None)
        elif upper.startswith(("ZED_PKG_", "ZED_EXTERNAL_")):
            environment.pop(key, None)
    environment["CI"] = "true"
    return environment


def run(
    command: Sequence[object],
    *,
    environment: dict[str, str],
    cwd: Path | None = None,
    expected: int | Iterable[int] = 0,
) -> subprocess.CompletedProcess[str]:
    expected_codes = {expected} if isinstance(expected, int) else set(expected)
    result = subprocess.run(
        [str(item) for item in command],
        cwd=cwd,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    if result.returncode not in expected_codes:
        rendered = " ".join(repr(str(item)) for item in command)
        raise ContractError(
            f"command returned {result.returncode}, expected {sorted(expected_codes)}: {rendered}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def parse_json_output(result: subprocess.CompletedProcess[str], label: str) -> dict:
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ContractError(
            f"{label} did not emit JSON: {error}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        ) from error
    require(isinstance(value, dict), f"{label} output must be a JSON object")
    return value


def canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def git_head(root: Path, environment: dict[str, str]) -> str:
    result = run(
        ["git", "-C", root, "rev-parse", "HEAD"], environment=environment
    )
    return result.stdout.strip()


def certify(args: argparse.Namespace) -> dict[str, object]:
    environment = sanitized_environment()
    zed = args.zed.resolve(strict=True)
    zed_gitops = args.zed_gitops.resolve(strict=True)
    cluster = args.cluster.resolve(strict=True)

    require(
        git_head(cluster, environment) == args.cluster_commit,
        "k8s-cluster checkout does not match the pinned commit",
    )
    product = args.product.resolve(strict=True)
    require(
        git_head(product, environment) == args.product_commit,
        "zed-cli checkout does not match the pinned commit",
    )

    checks: list[str] = ["immutable-checkouts-match"]

    native_check = parse_json_output(
        run(
            [
                sys.executable,
                cluster / "tools/gitops_composition.py",
                "check",
                "--root",
                cluster,
                "--format",
                "json",
            ],
            environment=environment,
        ),
        "native k8s-cluster validator",
    )
    require(native_check.get("valid") is True, f"native report invalid: {native_check}")
    require(native_check.get("errors") == 0, f"native errors: {native_check}")
    require(
        isinstance(native_check.get("records"), int) and native_check["records"] > 0,
        f"native validator found no records: {native_check}",
    )
    checks.append("native-k8s-validator-passes")

    native_render = parse_json_output(
        run(
            [
                sys.executable,
                cluster / "tools/gitops_composition.py",
                "render",
                "--root",
                cluster,
            ],
            environment=environment,
        ),
        "native k8s-cluster renderer",
    )
    require(
        native_render.get("kind") == "GitOpsApplicationPreviewList",
        f"unexpected preview kind: {native_render}",
    )
    rendered_items = native_render.get("items")
    require(isinstance(rendered_items, list), "preview items must be a list")
    require(
        len(rendered_items) == native_check["records"],
        "preview count does not match validated record count",
    )
    checks.append("native-preview-is-deterministic-and-complete")

    validator_args = [
        "validate",
        "--root",
        cluster,
        "--offline",
        "--strict",
        "--format",
        "json",
    ]
    direct_report = parse_json_output(
        run([zed_gitops, *validator_args], environment=environment),
        "standalone zed-gitops validator",
    )
    root_report = parse_json_output(
        run([zed, "gitops", *validator_args], environment=environment),
        "root-dispatched zed gitops validator",
    )
    require(direct_report == root_report, "root and standalone reports differ")
    require(root_report.get("valid") is True, f"zed report invalid: {root_report}")
    require(root_report.get("errors") == 0, f"zed report has errors: {root_report}")
    require(
        root_report.get("records") == native_check["records"],
        "native and Zed validators disagree on catalog record count",
    )
    checks.append("root-and-standalone-zed-validation-match")
    checks.append("native-and-zed-record-counts-match")

    record_path = cluster / "catalog/gitops/apps/dd-fabrication-server.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    spec = record.get("spec", {})
    inventory = spec.get("inventory", {})
    source = spec.get("source", {})
    migration = spec.get("migration", {})
    require(inventory.get("mode") == "git-submodule", "inventory mode drifted")
    require(source.get("mode") == "direct-repository", "source mode drifted")
    require(
        inventory.get("revision") == source.get("targetRevision"),
        "inventory gitlink and direct source revisions differ",
    )
    require(migration.get("phase") == "pilot-inert", "pilot is no longer inert")
    checks.append("catalog-preserves-exact-pin-and-inert-migration")

    application_set_path = (
        cluster
        / "remote/argocd/application-sets/gitops-composition-catalog-pilot.applicationset.yaml"
    )
    application_set = application_set_path.read_text(encoding="utf-8")
    required_fragments = (
        "name: gitops-composition-catalog-pilot",
        "oresoftware.dev/activation: inert-not-in-bootstrap",
        "missingkey=error",
        "path: catalog/gitops/apps/*.json",
        "targetRevision: '{{ .spec.source.targetRevision }}'",
        "name: 'catalog-pilot-{{ .metadata.name }}'",
    )
    for fragment in required_fragments:
        require(fragment in application_set, f"ApplicationSet contract omitted: {fragment}")
    checks.append("applicationset-template-remains-fail-closed")

    references: list[str] = []
    for path in (cluster / "remote/argocd").rglob("*"):
        if not path.is_file() or path == application_set_path:
            continue
        if path.suffix.lower() not in {".yaml", ".yml", ".json"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if (
            "gitops-composition-catalog-pilot" in text
            or application_set_path.name in text
        ):
            references.append(str(path.relative_to(cluster)))
    require(not references, f"pilot is referenced by live Argo material: {references}")
    checks.append("applicationset-pilot-is-unreferenced")

    return {
        "$schema": "zed-pkg-test/real-k8s-gitops-contract/v1",
        "productCommit": args.product_commit,
        "clusterCommit": args.cluster_commit,
        "result": "passed",
        "recordCount": root_report["records"],
        "renderedApplicationCount": len(rendered_items),
        "nativeReportSha256": canonical_digest(native_check),
        "zedReportSha256": canonical_digest(root_report),
        "previewSha256": canonical_digest(native_render),
        "checkCount": len(checks),
        "checks": checks,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", type=Path, required=True)
    parser.add_argument("--zed-gitops", type=Path, required=True)
    parser.add_argument("--product", type=Path, required=True)
    parser.add_argument("--cluster", type=Path, required=True)
    parser.add_argument("--product-commit", required=True)
    parser.add_argument("--cluster-commit", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        evidence = certify(args)
    except (ContractError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"certified {evidence['checkCount']} real GitOps checks across "
        f"{evidence['recordCount']} catalog record(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
