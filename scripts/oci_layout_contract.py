#!/usr/bin/env python3
"""Black-box certification for local `zed oci plan --out` layouts."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from oci_plan_contract import (
    DIGEST_RE,
    LOCK_LAYER,
    MANIFEST_LAYER,
    OCI_MANIFEST,
    PACKAGE_LAYER,
    ZED_CONFIG,
    Harness,
    copy_fixture,
    fingerprint,
    poisoned_environment,
    transaction_sentinel,
    write_lock,
)

OCI_INDEX = "application/vnd.oci.image.index.v1+json"
OCI_LAYOUT_VERSION = "1.0.0"
REF_NAME = "org.opencontainers.image.ref.name"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", required=True, type=Path)
    parser.add_argument("--strace", required=True, type=Path)
    parser.add_argument("--node-lib", required=True, type=Path)
    parser.add_argument("--node-app", required=True, type=Path)
    parser.add_argument("--polyglot-lib", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    return parser.parse_args()


def layout_command(
    destination: str,
    output: Path,
    target: str | None = None,
) -> list[str]:
    command = ["oci", "plan", destination]
    if target is not None:
        command.extend(["--target", target])
    command.extend(["--out", str(output), "--json"])
    return command


def plan_command(destination: str, target: str | None = None) -> list[str]:
    command = ["oci", "plan", destination]
    if target is not None:
        command.extend(["--target", target])
    command.append("--json")
    return command


def parse_json(stdout: str, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(f"{label} stdout is not JSON: {stdout}") from error
    if not isinstance(payload, dict):
        raise AssertionError(f"{label} JSON must be an object")
    return payload


def canonical_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or DIGEST_RE.fullmatch(value) is None:
        raise AssertionError(f"{label} is not a canonical OCI digest: {value!r}")
    return value


def descriptor_blob(layout: Path, descriptor: dict[str, Any], label: str) -> bytes:
    digest = canonical_digest(descriptor.get("digest"), f"{label} digest")
    size = descriptor.get("size")
    if not isinstance(size, int) or size <= 0:
        raise AssertionError(f"{label} size is invalid: {size!r}")
    blob = layout / "blobs" / "sha256" / digest.removeprefix("sha256:")
    if not blob.is_file():
        raise AssertionError(f"{label} blob is missing: {blob}")
    content = blob.read_bytes()
    if len(content) != size:
        raise AssertionError(f"{label} size drift: descriptor={size} actual={len(content)}")
    actual = "sha256:" + hashlib.sha256(content).hexdigest()
    if actual != digest:
        raise AssertionError(f"{label} digest drift: descriptor={digest} actual={actual}")
    return content


def validate_layout(
    layout: Path,
    result: dict[str, Any],
    *,
    expected_tag: str,
    has_lock: bool,
) -> str:
    assert result["schema"] == "zed.oci-layout-result/v1"
    assert Path(result["path"]) == layout
    assert result["requested_destination"]["tag"] == expected_tag
    resolved = result["resolved_reference"]
    manifest_digest = canonical_digest(resolved["digest"], "resolved reference")
    assert result["manifest"]["digest"] == manifest_digest

    layout_version = json.loads((layout / "oci-layout").read_text(encoding="utf-8"))
    assert layout_version == {"imageLayoutVersion": OCI_LAYOUT_VERSION}
    index = json.loads((layout / "index.json").read_text(encoding="utf-8"))
    assert index["schemaVersion"] == 2
    assert index["mediaType"] == OCI_INDEX
    assert len(index["manifests"]) == 1
    manifest_descriptor = index["manifests"][0]
    assert manifest_descriptor["mediaType"] == OCI_MANIFEST
    assert manifest_descriptor["digest"] == manifest_digest
    assert manifest_descriptor["annotations"][REF_NAME] == expected_tag

    manifest = json.loads(descriptor_blob(layout, manifest_descriptor, "OCI manifest"))
    assert manifest["schemaVersion"] == 2
    assert manifest["mediaType"] == OCI_MANIFEST
    assert manifest["artifactType"] == ZED_CONFIG
    config = manifest["config"]
    assert config["mediaType"] == ZED_CONFIG
    descriptor_blob(layout, config, "OCI config")

    layers = manifest["layers"]
    media_types = {layer["mediaType"] for layer in layers}
    expected_media_types = {PACKAGE_LAYER, MANIFEST_LAYER}
    if has_lock:
        expected_media_types.add(LOCK_LAYER)
    assert media_types == expected_media_types
    for layer in layers:
        descriptor_blob(layout, layer, f"layer {layer['mediaType']}")

    expected_digests = {
        manifest_descriptor["digest"],
        config["digest"],
        *(layer["digest"] for layer in layers),
    }
    actual_files = {
        "sha256:" + path.name
        for path in (layout / "blobs" / "sha256").iterdir()
        if path.is_file()
    }
    assert actual_files == expected_digests
    assert result["blob_count"] == len(expected_digests)
    expected_bytes = manifest_descriptor["size"] + config["size"] + sum(
        layer["size"] for layer in layers
    )
    assert result["total_blob_bytes"] == expected_bytes

    digest = hashlib.sha256()
    for path in sorted(layout.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_file():
            digest.update(path.relative_to(layout).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def successful_layout(
    harness: Harness,
    project: Path,
    destination: str,
    output: Path,
    env: dict[str, str],
    *,
    expected_tag: str,
    target: str | None = None,
    has_lock: bool = False,
    audit: bool = False,
) -> tuple[dict[str, Any], str]:
    before = fingerprint(project)
    result = harness.invoke(
        project,
        layout_command(destination, output, target),
        env,
        success=True,
        audit=audit,
    )
    if before != fingerprint(project):
        raise AssertionError(f"OCI layout materialization mutated project tree: {project}")
    if (project / ".zed" / "pack").exists():
        raise AssertionError("OCI layout materialization left persistent .zed/pack output")
    payload = parse_json(result.stdout, "OCI layout")
    return payload, validate_layout(
        output,
        payload,
        expected_tag=expected_tag,
        has_lock=has_lock,
    )


def compare_plan(
    harness: Harness,
    project: Path,
    destination: str,
    layout_result: dict[str, Any],
    env: dict[str, str],
    *,
    target: str | None = None,
) -> None:
    result = harness.invoke(
        project,
        plan_command(destination, target),
        env,
        success=True,
    )
    plan = parse_json(result.stdout, "OCI plan")
    assert plan["requested_destination"] == layout_result["requested_destination"]
    assert plan["resolved_reference"] == layout_result["resolved_reference"]
    assert plan["adapter"]["manifest"] == layout_result["manifest"]


def save_evidence(harness: Harness, summary: dict[str, Any]) -> None:
    payload = {
        "schema": "zed-pkg-test.oci-layout-evidence/v1",
        "summary": summary,
        "invocations": harness.records,
    }
    (harness.evidence / "evidence.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = arguments()
    harness = Harness(args.zed, args.evidence, args.strace)
    summary: dict[str, Any] = {"layouts": {}, "negative_cases": []}
    try:
        with tempfile.TemporaryDirectory(prefix="zed-oci-layout-contract-") as temporary:
            work = Path(temporary)
            env = poisoned_environment(work)

            node = copy_fixture(args.node_lib, work / "node-lib")
            node_marker = transaction_sentinel(node)
            destination = "oci://ghcr.io/zed-pkg-test/node-lib:1.0.0"
            first_result, first_fingerprint = successful_layout(
                harness,
                node,
                destination,
                work / "node-layout-a",
                env,
                expected_tag="1.0.0",
                audit=True,
            )
            second_result, second_fingerprint = successful_layout(
                harness,
                node,
                destination,
                work / "node-layout-b",
                env,
                expected_tag="1.0.0",
            )
            first_without_path = {
                key: value for key, value in first_result.items() if key != "path"
            }
            second_without_path = {
                key: value for key, value in second_result.items() if key != "path"
            }
            assert first_without_path == second_without_path
            assert first_fingerprint == second_fingerprint
            compare_plan(harness, node, destination, first_result, env)
            assert node_marker.is_file()
            summary["layouts"]["node-lib"] = first_fingerprint

            existing = work / "existing-layout"
            existing.mkdir()
            marker = existing / "keep.txt"
            marker.write_text("must remain\n", encoding="utf-8")
            before = fingerprint(node)
            harness.invoke(
                node,
                layout_command(destination, existing),
                env,
                success=False,
                diagnostic="refusing to replace existing OCI layout output",
            )
            assert marker.read_text(encoding="utf-8") == "must remain\n"
            assert before == fingerprint(node)
            summary["negative_cases"].append("existing-output")

            poly = copy_fixture(args.polyglot_lib, work / "polyglot-lib")
            poly_marker = transaction_sentinel(poly)
            poly_destination = "oci://ghcr.io/zedtest/polyglot-lib-rust:0.1.0"
            poly_result, poly_fingerprint = successful_layout(
                harness,
                poly,
                poly_destination,
                work / "polyglot-rust-layout",
                env,
                expected_tag="0.1.0",
                target="rust",
            )
            compare_plan(
                harness,
                poly,
                poly_destination,
                poly_result,
                env,
                target="rust",
            )
            assert poly_marker.is_file()
            summary["layouts"]["polyglot-lib-rust"] = poly_fingerprint

            app = copy_fixture(args.node_app, work / "node-app")
            app_marker = transaction_sentinel(app)
            write_lock(app / ".zpkg.lock", "1.0.0", "a" * 64)
            app_destination = "oci://ghcr.io/zed-pkg-test/node-app:0.1.0"
            app_result, app_fingerprint = successful_layout(
                harness,
                app,
                app_destination,
                work / "node-app-layout",
                env,
                expected_tag="0.1.0",
                has_lock=True,
            )
            compare_plan(harness, app, app_destination, app_result, env)
            assert app_marker.is_file()
            summary["layouts"]["node-app"] = app_fingerprint

            summary["layout_count"] = len(summary["layouts"])
            summary["negative_case_count"] = len(summary["negative_cases"])
            summary["invocation_count"] = len(harness.records)
            assert summary["layout_count"] == 3
            assert summary["negative_case_count"] == 1
    finally:
        save_evidence(harness, summary)

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
