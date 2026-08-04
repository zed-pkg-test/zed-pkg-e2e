#!/usr/bin/env python3
"""Black-box certification for `zed oci push` against an authenticated registry."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from oci_layout_contract import canonical_digest, parse_json
from oci_plan_contract import (
    DIGEST_RE,
    copy_fixture,
    fingerprint,
    poisoned_environment,
    transaction_sentinel,
)

TEMP_AUTH_PREFIX = "zed-oci-auth-"
SECRET_REPLACEMENT = "<redacted>"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", required=True, type=Path)
    parser.add_argument("--oras", required=True, type=Path)
    parser.add_argument("--strace", required=True, type=Path)
    parser.add_argument("--node-lib", required=True, type=Path)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--evidence", required=True, type=Path)
    return parser.parse_args()


class PushHarness:
    def __init__(
        self,
        zed: Path,
        oras: Path,
        strace: Path,
        evidence: Path,
        secret: str,
    ) -> None:
        self.zed = zed.resolve()
        self.oras = oras.resolve()
        self.strace = strace.resolve()
        self.evidence = evidence.resolve()
        self.secret = secret
        self.records: list[dict[str, Any]] = []
        self.trace_index = 0
        self.evidence.mkdir(parents=True, exist_ok=True)
        for label, executable in (
            ("zed", self.zed),
            ("oras", self.oras),
            ("strace", self.strace),
        ):
            if not executable.is_file() or not os.access(executable, os.X_OK):
                raise AssertionError(f"{label} is not executable: {executable}")

    def invoke(
        self,
        cwd: Path,
        args: list[str],
        env: dict[str, str],
        *,
        success: bool,
        stdin: str | None = None,
        diagnostic: str | None = None,
        audit: bool = False,
        timeout: int = 180,
    ) -> subprocess.CompletedProcess[str]:
        argv = [str(self.zed), *args]
        command = argv
        trace_file: Path | None = None
        if audit:
            self.trace_index += 1
            trace_file = self.evidence / f"strace-{self.trace_index:02d}.log"
            command = [
                str(self.strace),
                "-f",
                "-qq",
                "-e",
                "trace=network,openat",
                "-o",
                str(trace_file),
                *argv,
            ]

        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            input=stdin,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        combined = result.stdout + result.stderr
        if self.secret in combined:
            raise AssertionError("OCI push leaked the registry password to output")
        self.records.append(
            {
                "argv": argv,
                "cwd": str(cwd),
                "returncode": result.returncode,
                "stdout": redact(result.stdout, self.secret),
                "stderr": redact(result.stderr, self.secret),
                "stdin_supplied": stdin is not None,
                "audited": audit,
                "trace_file": str(trace_file) if trace_file else None,
            }
        )

        if success != (result.returncode == 0):
            expectation = "succeed" if success else "fail"
            raise AssertionError(
                f"expected command to {expectation}: {' '.join(argv)}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        if diagnostic is not None and diagnostic not in combined:
            raise AssertionError(
                f"missing diagnostic {diagnostic!r}: {' '.join(argv)}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        if trace_file is not None:
            trace = trace_file.read_text(encoding="utf-8", errors="replace")
            if self.secret in trace:
                raise AssertionError("OCI push leaked the registry password to syscall evidence")
            forbidden = [
                Path(env["HOME"]) / ".docker" / "config.json",
                Path(env["DOCKER_CONFIG"]) / "config.json",
                Path(env["ZED_PKG_HOME"]) / "credentials.toml",
                Path(env["ZED_PKG_HOME"]) / "auth" / "sessions.toml",
            ]
            opened = [str(path) for path in forbidden if str(path) in trace]
            if opened:
                raise AssertionError(
                    "explicit OCI push opened implicit credential state: "
                    + ", ".join(opened)
                )
        return result

    def save(self, summary: dict[str, Any]) -> None:
        payload = {
            "schema": "zed-pkg-test.oci-oras-push-evidence/v1",
            "summary": summary,
            "invocations": self.records,
        }
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        if self.secret in text:
            raise AssertionError("registry password leaked into structured evidence")
        (self.evidence / "evidence.json").write_text(text, encoding="utf-8")


def redact(value: str, secret: str) -> str:
    return value.replace(secret, SECRET_REPLACEMENT)


def registry_environment(work: Path) -> dict[str, str]:
    env = poisoned_environment(work)
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        env.pop(key, None)
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = env["NO_PROXY"]

    docker_config = work / "implicit-docker-config"
    docker_config.mkdir()
    poison = docker_config / "config.json"
    poison.write_text("this implicit Docker config must never be opened\n", encoding="utf-8")
    poison.chmod(0)
    env["DOCKER_CONFIG"] = str(docker_config)
    env.pop("REGISTRY_AUTH_FILE", None)
    return env


def layout_command(destination: str, output: Path) -> list[str]:
    return ["oci", "plan", destination, "--out", str(output), "--json"]


def push_command(
    harness: PushHarness,
    layout: Path,
    destination: str,
    username: str,
    *,
    allow_replacement: bool = False,
) -> list[str]:
    command = [
        "oci",
        "push",
        str(layout),
        destination,
        "--oras",
        str(harness.oras),
        "--username",
        username,
        "--password-stdin",
        "--plain-http",
        "--json",
    ]
    if allow_replacement:
        command.append("--allow-tag-replacement")
    return command


def materialize_layout(
    harness: PushHarness,
    project: Path,
    destination: str,
    layout: Path,
    env: dict[str, str],
) -> dict[str, Any]:
    before = fingerprint(project)
    result = harness.invoke(
        project,
        layout_command(destination, layout),
        env,
        success=True,
    )
    if fingerprint(project) != before:
        raise AssertionError("OCI layout materialization mutated the source fixture")
    if (project / ".zed" / "pack").exists():
        raise AssertionError("OCI layout materialization left persistent .zed/pack output")
    payload = parse_json(result.stdout, "OCI layout")
    assert payload["schema"] == "zed.oci-layout-result/v1"
    assert Path(payload["path"]) == layout
    canonical_digest(payload["manifest"]["digest"], "layout manifest")
    return payload


def authenticated_push(
    harness: PushHarness,
    project: Path,
    layout: Path,
    destination: str,
    username: str,
    password: str,
    env: dict[str, str],
    *,
    success: bool,
    status: str | None = None,
    diagnostic: str | None = None,
    allow_replacement: bool = False,
    audit: bool = False,
) -> dict[str, Any] | None:
    before = fingerprint(project)
    temporary_before = temporary_auth_directories()
    result = harness.invoke(
        project,
        push_command(
            harness,
            layout,
            destination,
            username,
            allow_replacement=allow_replacement,
        ),
        env,
        stdin=password + "\n",
        success=success,
        diagnostic=diagnostic,
        audit=audit,
    )
    if fingerprint(project) != before:
        raise AssertionError("OCI push mutated the source fixture")
    leaked_temporary = temporary_auth_directories() - temporary_before
    if leaked_temporary:
        raise AssertionError(
            "OCI push left temporary registry credentials behind: "
            + ", ".join(sorted(leaked_temporary))
        )
    if not success:
        return None
    payload = parse_json(result.stdout, "OCI push")
    assert payload["schema"] == "zed.oci-push-result/v1"
    assert payload["authentication"] == "password-stdin"
    assert payload["transport"] == "oras-cp"
    assert payload["plain_http"] is True
    assert payload["insecure_tls"] is False
    if status is not None:
        assert payload["status"] == status
    canonical_digest(payload["manifest"]["digest"], "push manifest")
    assert payload["destination"]["digest"] == payload["manifest"]["digest"]
    return payload


def temporary_auth_directories() -> set[str]:
    root = Path(tempfile.gettempdir())
    return {
        str(path.resolve())
        for path in root.glob(TEMP_AUTH_PREFIX + "*")
        if path.exists()
    }


def independent_registry_config(
    work: Path,
    registry: str,
    username: str,
    password: str,
) -> Path:
    auth = base64.b64encode(f"{username}:{password}".encode()).decode()
    path = work / "independent-registry-config.json"
    path.write_text(
        json.dumps({"auths": {registry: {"auth": auth}}}, sort_keys=True),
        encoding="utf-8",
    )
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return path


def resolve_digest(
    oras: Path,
    registry_config: Path,
    target: str,
    env: dict[str, str],
    password: str,
) -> str:
    result = subprocess.run(
        [
            str(oras),
            "resolve",
            "--registry-config",
            str(registry_config),
            "--plain-http",
            target,
        ],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )
    if password in result.stdout + result.stderr:
        raise AssertionError("independent ORAS verification leaked the password")
    if result.returncode != 0:
        raise AssertionError(
            f"independent ORAS resolve failed ({result.returncode})\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    candidates = re.findall(r"sha256:[0-9a-f]{64}", result.stdout + result.stderr)
    if not candidates:
        raise AssertionError("independent ORAS resolve returned no canonical digest")
    return candidates[-1]


def tamper_package_blob(layout: Path) -> None:
    index = json.loads((layout / "index.json").read_text(encoding="utf-8"))
    manifest_digest = canonical_digest(
        index["manifests"][0]["digest"], "tamper manifest descriptor"
    )
    manifest_path = layout / "blobs" / "sha256" / manifest_digest.removeprefix("sha256:")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    package = manifest["layers"][0]
    package_digest = canonical_digest(package["digest"], "tamper package descriptor")
    package_path = layout / "blobs" / "sha256" / package_digest.removeprefix("sha256:")
    package_path.write_bytes(package_path.read_bytes() + b"tampered")


def main() -> int:
    args = arguments()
    if not args.password or "\n" in args.password or "\r" in args.password:
        raise AssertionError("test password must be one non-empty line")
    registry = args.registry.rstrip("/")
    destination = f"oci://{registry}/zed-pkg-test/node-lib:1.0.0"
    target = f"{registry}/zed-pkg-test/node-lib:1.0.0"
    harness = PushHarness(
        args.zed,
        args.oras,
        args.strace,
        args.evidence,
        args.password,
    )
    summary: dict[str, Any] = {"successful": [], "negative": []}

    try:
        with tempfile.TemporaryDirectory(prefix="zed-oci-oras-contract-") as temporary:
            work = Path(temporary)
            env = registry_environment(work)
            project = copy_fixture(args.node_lib, work / "node-lib")
            transaction = transaction_sentinel(project)
            registry_config = independent_registry_config(
                work, registry, args.username, args.password
            )

            first_layout = work / "layout-a"
            first_layout_result = materialize_layout(
                harness, project, destination, first_layout, env
            )
            first_digest = first_layout_result["manifest"]["digest"]
            first_push = authenticated_push(
                harness,
                project,
                first_layout,
                destination,
                args.username,
                args.password,
                env,
                success=True,
                status="pushed",
                audit=True,
            )
            assert first_push is not None
            assert first_push["manifest"]["digest"] == first_digest
            assert resolve_digest(
                harness.oras, registry_config, target, env, args.password
            ) == first_digest
            summary["successful"].append("authenticated-first-push")

            idempotent = authenticated_push(
                harness,
                project,
                first_layout,
                destination,
                args.username,
                args.password,
                env,
                success=True,
                status="already-present",
            )
            assert idempotent is not None
            assert idempotent["manifest"]["digest"] == first_digest
            summary["successful"].append("idempotent-same-digest")

            replacement_project = copy_fixture(args.node_lib, work / "node-lib-replacement")
            replacement_transaction = transaction_sentinel(replacement_project)
            (replacement_project / "oci-replacement.txt").write_text(
                "this content deliberately changes the package artifact\n",
                encoding="utf-8",
            )
            replacement_layout = work / "layout-b"
            replacement_layout_result = materialize_layout(
                harness,
                replacement_project,
                destination,
                replacement_layout,
                env,
            )
            replacement_digest = replacement_layout_result["manifest"]["digest"]
            assert replacement_digest != first_digest
            authenticated_push(
                harness,
                replacement_project,
                replacement_layout,
                destination,
                args.username,
                args.password,
                env,
                success=False,
                diagnostic="refusing to replace OCI tag",
            )
            assert resolve_digest(
                harness.oras, registry_config, target, env, args.password
            ) == first_digest
            summary["negative"].append("replacement-without-consent")

            replacement_push = authenticated_push(
                harness,
                replacement_project,
                replacement_layout,
                destination,
                args.username,
                args.password,
                env,
                success=True,
                status="replaced",
                allow_replacement=True,
            )
            assert replacement_push is not None
            assert replacement_push["manifest"]["digest"] == replacement_digest
            assert resolve_digest(
                harness.oras, registry_config, target, env, args.password
            ) == replacement_digest
            summary["successful"].append("explicit-tag-replacement")

            tampered_layout = work / "layout-tampered"
            shutil.copytree(first_layout, tampered_layout)
            tamper_package_blob(tampered_layout)
            authenticated_push(
                harness,
                project,
                tampered_layout,
                destination,
                args.username,
                args.password,
                env,
                success=False,
                diagnostic="size drift",
            )
            summary["negative"].append("tampered-layout-before-transport")

            before = fingerprint(project)
            harness.invoke(
                project,
                [
                    "oci",
                    "push",
                    str(first_layout),
                    destination,
                    "--oras",
                    str(harness.oras),
                    "--plain-http",
                    "--json",
                ],
                env,
                success=False,
                diagnostic="choose exactly one OCI authentication mode",
            )
            assert fingerprint(project) == before
            summary["negative"].append("implicit-auth-rejected")

            harness.invoke(
                project,
                [
                    "oci",
                    "push",
                    str(first_layout),
                    "oci://ghcr.io/zed-pkg-test/node-lib:1.0.0",
                    "--oras",
                    str(harness.oras),
                    "--anonymous",
                    "--plain-http",
                ],
                env,
                success=False,
                diagnostic="--plain-http is accepted only for loopback registries",
            )
            summary["negative"].append("plain-http-non-loopback")

            assert transaction.is_file()
            assert replacement_transaction.is_file()
            assert not (project / ".zed" / "pack").exists()
            assert not (replacement_project / ".zed" / "pack").exists()
            summary["final_digest"] = replacement_digest
            summary["successful_count"] = len(summary["successful"])
            summary["negative_count"] = len(summary["negative"])
            summary["invocation_count"] = len(harness.records)
            assert summary["successful_count"] == 3
            assert summary["negative_count"] == 4
    finally:
        harness.save(summary)

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
