#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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
EXPECTED_REPOSITORY = "github.com/daedalus-fab/fabrication-server.rs"
EXPECTED_KINDS = {"Deployment", "ExternalSecret", "NetworkPolicy", "Service"}
CLUSTER_SCOPED_KINDS = {
    "ClusterRole",
    "ClusterRoleBinding",
    "CustomResourceDefinition",
    "MutatingWebhookConfiguration",
    "Namespace",
    "Node",
    "PersistentVolume",
    "PriorityClass",
    "StorageClass",
    "ValidatingWebhookConfiguration",
}


class CertificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CertificationError(message)


def sanitized_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for key in list(environment):
        upper = key.upper()
        if any(fragment in upper for fragment in SENSITIVE_ENV_FRAGMENTS):
            environment.pop(key, None)
        elif upper.startswith(("ZED_PKG_", "ZED_EXTERNAL_", "ZED_PROBE_")):
            environment.pop(key, None)
    environment["CI"] = "true"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def run(
    command: Sequence[object],
    *,
    environment: dict[str, str],
    cwd: Path | None = None,
    expected: int | Iterable[int] = 0,
    timeout: int = 120,
) -> subprocess.CompletedProcess[bytes]:
    expected_codes = {expected} if isinstance(expected, int) else set(expected)
    result = subprocess.run(
        [str(item) for item in command],
        cwd=cwd,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if result.returncode not in expected_codes:
        rendered = " ".join(repr(str(item)) for item in command)
        raise CertificationError(
            f"command returned {result.returncode}, expected {sorted(expected_codes)}: {rendered}\n"
            f"stdout:\n{result.stdout.decode(errors='replace')}\n"
            f"stderr:\n{result.stderr.decode(errors='replace')}"
        )
    return result


def text(result: subprocess.CompletedProcess[bytes]) -> str:
    return result.stdout.decode("utf-8", errors="strict").strip()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_repository(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    if normalized.startswith("git@github.com:"):
        normalized = "github.com/" + normalized.removeprefix("git@github.com:")
    elif normalized.startswith("ssh://git@github.com/"):
        normalized = "github.com/" + normalized.removeprefix("ssh://git@github.com/")
    elif normalized.startswith("https://github.com/"):
        normalized = "github.com/" + normalized.removeprefix("https://github.com/")
    elif normalized.startswith("http://github.com/"):
        normalized = "github.com/" + normalized.removeprefix("http://github.com/")
    return normalized.removesuffix("/").removesuffix(".git").lower()


def git_head(root: Path, environment: dict[str, str]) -> str:
    return text(run(["git", "-C", root, "rev-parse", "HEAD"], environment=environment))


def gitmodule_for_path(
    cluster: Path,
    path: str,
    environment: dict[str, str],
) -> tuple[str, str]:
    result = run(
        [
            "git",
            "-C",
            cluster,
            "config",
            "-f",
            ".gitmodules",
            "--get-regexp",
            r"^submodule\..*\.path$",
        ],
        environment=environment,
    )
    selected_key: str | None = None
    for raw_line in text(result).splitlines():
        key, _, value = raw_line.partition(" ")
        if value.strip() == path:
            selected_key = key.removesuffix(".path")
            break
    require(selected_key is not None, f".gitmodules has no entry for {path}")
    url = text(
        run(
            [
                "git",
                "-C",
                cluster,
                "config",
                "-f",
                ".gitmodules",
                "--get",
                f"{selected_key}.url",
            ],
            environment=environment,
        )
    )
    return selected_key, url


def indexed_gitlink(
    cluster: Path,
    path: str,
    environment: dict[str, str],
) -> tuple[str, str, str, str]:
    output = text(
        run(
            ["git", "-C", cluster, "ls-tree", "HEAD", "--", path],
            environment=environment,
        )
    )
    require(output, f"Git index has no entry for {path}")
    left, separator, indexed_path = output.partition("\t")
    require(separator == "\t", f"unexpected ls-tree output: {output}")
    mode, object_type, revision = left.split()
    return mode, object_type, revision, indexed_path


def parse_rendered_documents(rendered: bytes) -> list[dict[str, str]]:
    source = rendered.decode("utf-8", errors="strict")
    require(source.strip(), "Kustomize emitted empty output")
    require("\r\n" not in source, "Kustomize output contains platform-specific CRLF")
    documents: list[dict[str, str]] = []
    for index, document in enumerate(re.split(r"(?m)^---\s*$", source), start=1):
        if not document.strip():
            continue
        api_match = re.search(r"(?m)^apiVersion:\s*([^\s#]+)\s*$", document)
        kind_match = re.search(r"(?m)^kind:\s*([^\s#]+)\s*$", document)
        metadata_match = re.search(r"(?m)^metadata:\s*$", document)
        name_match = re.search(r"(?m)^  name:\s*([^\s#]+)\s*$", document)
        namespace_match = re.search(r"(?m)^  namespace:\s*([^\s#]+)\s*$", document)
        require(api_match is not None, f"rendered document {index} lacks apiVersion")
        require(kind_match is not None, f"rendered document {index} lacks kind")
        require(metadata_match is not None, f"rendered document {index} lacks metadata")
        require(name_match is not None, f"rendered document {index} lacks metadata.name")
        require(
            namespace_match is not None and namespace_match.group(1) == "daedalus",
            f"rendered document {index} is not explicitly namespaced to daedalus",
        )
        kind = kind_match.group(1)
        require(kind not in CLUSTER_SCOPED_KINDS, f"cluster-scoped resource rendered: {kind}")
        documents.append(
            {
                "apiVersion": api_match.group(1),
                "kind": kind,
                "name": name_match.group(1),
                "namespace": namespace_match.group(1),
            }
        )
    require(documents, "Kustomize emitted no YAML documents")
    return documents


def certify(args: argparse.Namespace) -> dict[str, object]:
    environment = sanitized_environment()
    cluster = args.cluster.resolve(strict=True)
    child = args.child.resolve(strict=True)
    kustomize = args.kustomize.resolve(strict=True)
    install_metadata = json.loads(args.kustomize_metadata.read_text(encoding="utf-8"))

    require(git_head(cluster, environment) == args.cluster_commit, "cluster checkout SHA drift")
    require(git_head(child, environment) == args.child_commit, "child checkout SHA drift")

    record_path = cluster / "catalog/gitops/apps/dd-fabrication-server.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    require(isinstance(record, dict), "catalog record must be an object")
    require(record.get("metadata", {}).get("name") == "dd-fabrication-server", "catalog app name drift")
    spec = record.get("spec")
    require(isinstance(spec, dict), "catalog spec must be an object")
    inventory = spec.get("inventory")
    source = spec.get("source")
    migration = spec.get("migration")
    require(isinstance(inventory, dict), "catalog inventory must be an object")
    require(isinstance(source, dict), "catalog source must be an object")
    require(isinstance(migration, dict), "catalog migration must be an object")

    inventory_path = str(inventory.get("path", ""))
    source_path = str(source.get("path", ""))
    require(inventory.get("mode") == "git-submodule", "inventory mode drift")
    require(inventory_path == "remote/deployments/fabrication-server-rs", "inventory path drift")
    require(inventory.get("revision") == args.child_commit, "inventory revision differs from child checkout")
    require(source.get("mode") == "direct-repository", "source is not direct-repository")
    require(source.get("renderer") == "kustomize", "source renderer is not kustomize")
    require(source_path == "k8s", "source path drift")
    require(source.get("targetRevision") == args.child_commit, "source targetRevision differs from child checkout")
    require(migration.get("phase") == "pilot-inert", "migration phase is no longer pilot-inert")

    inventory_repository = canonical_repository(str(inventory.get("repository", "")))
    source_repository = canonical_repository(str(source.get("repository", "")))
    require(inventory_repository == EXPECTED_REPOSITORY, "inventory repository identity drift")
    require(source_repository == EXPECTED_REPOSITORY, "source repository identity drift")
    require(inventory_repository == source_repository, "inventory/source repository mismatch")

    module_key, module_url = gitmodule_for_path(cluster, inventory_path, environment)
    require(canonical_repository(module_url) == EXPECTED_REPOSITORY, ".gitmodules repository identity drift")
    mode, object_type, indexed_revision, indexed_path = indexed_gitlink(
        cluster, inventory_path, environment
    )
    require(mode == "160000", f"inventory entry mode is {mode}, not 160000")
    require(object_type == "commit", f"inventory entry type is {object_type}, not commit")
    require(indexed_path == inventory_path, "indexed gitlink path drift")
    require(indexed_revision == args.child_commit, "indexed gitlink differs from child checkout")
    require(indexed_revision == inventory.get("revision"), "indexed gitlink differs from catalog inventory")
    require(indexed_revision == source.get("targetRevision"), "indexed gitlink differs from Argo source")

    require(
        not (cluster / inventory_path / source_path / "kustomization.yaml").exists(),
        "cluster checkout unexpectedly materialized the child source; direct-source proof is ambiguous",
    )
    child_source = child / source_path
    require((child_source / "kustomization.yaml").is_file(), "child source lacks kustomization.yaml")

    version_result = run([kustomize, "version"], environment=environment)
    version_output = text(version_result)
    require("v5.8.1" in version_output, f"unexpected Kustomize version: {version_output}")
    render_one = run([kustomize, "build", child_source], environment=environment, timeout=180).stdout
    render_two = run([kustomize, "build", child_source], environment=environment, timeout=180).stdout
    require(render_one == render_two, "repeated Kustomize renders differ")

    documents = parse_rendered_documents(render_one)
    kinds = {document["kind"] for document in documents}
    require(kinds == EXPECTED_KINDS, f"unexpected rendered resource kinds: {sorted(kinds)}")
    require(len(documents) == len(EXPECTED_KINDS), "rendered duplicate or missing resource kinds")
    rendered_text = render_one.decode("utf-8")
    require(not re.search(r"(?m)^kind:\s*Secret\s*$", rendered_text), "plaintext Secret resource rendered")
    require(not re.search(r"(?m)^stringData:\s*$", rendered_text), "plaintext stringData rendered")

    args.rendered.parent.mkdir(parents=True, exist_ok=True)
    args.rendered.write_bytes(render_one)

    return {
        "$schema": "zed-pkg-test/direct-source-render/v1",
        "clusterCommit": args.cluster_commit,
        "childCommit": args.child_commit,
        "catalog": {
            "name": record["metadata"]["name"],
            "inventoryPath": inventory_path,
            "sourcePath": source_path,
            "repository": source_repository,
            "moduleKey": module_key,
            "gitlinkRevision": indexed_revision,
            "migrationPhase": migration["phase"],
        },
        "kustomize": {
            "versionOutput": version_output,
            "asset": install_metadata.get("asset"),
            "assetSha256": install_metadata.get("assetSha256"),
            "executableSha256": install_metadata.get("executableSha256"),
        },
        "render": {
            "sha256": sha256_bytes(render_one),
            "byteCount": len(render_one),
            "documentCount": len(documents),
            "resources": documents,
        },
        "result": "passed",
        "checks": [
            "exact-public-checkout-identities",
            "catalog-inventory-source-child-parity",
            "gitmodules-and-indexed-gitlink-parity",
            "direct-source-not-materialized-in-superproject",
            "checksum-pinned-kustomize-version",
            "deterministic-repeat-render",
            "namespace-scoped-resource-set",
            "no-plaintext-secret-resource",
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cluster", type=Path, required=True)
    parser.add_argument("--child", type=Path, required=True)
    parser.add_argument("--cluster-commit", required=True)
    parser.add_argument("--child-commit", required=True)
    parser.add_argument("--kustomize", type=Path, required=True)
    parser.add_argument("--kustomize-metadata", type=Path, required=True)
    parser.add_argument("--rendered", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        evidence = certify(args)
    except (CertificationError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"rendered {evidence['render']['documentCount']} direct-source resources "
        f"at {evidence['render']['sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
