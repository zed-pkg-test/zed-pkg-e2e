#!/usr/bin/env python3
"""Black-box certification for `zed oci plan`.

The harness executes one exact zed binary against immutable fixture checkouts.
It validates deterministic OCI identities, fail-closed input handling, frozen
lock provenance, polyglot target selection, project immutability, and the
credential/network-free planning boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
ZED_CONFIG_MEDIA_TYPE = "application/vnd.zed.package.config.v1+json"
PACKAGE_MEDIA_TYPE = "application/vnd.zed.package.v1.tar+gzip"
MANIFEST_MEDIA_TYPE = "application/vnd.zed.package.manifest.v1+toml"
LOCK_MEDIA_TYPE = "application/vnd.zed.package.lock.v1+toml"


@dataclass(frozen=True)
class Invocation:
    argv: list[str]
    cwd: str
    returncode: int
    stdout: str
    stderr: str
    audited: bool
    trace_file: str | None


class ContractHarness:
    def __init__(self, zed: Path, evidence: Path, strace: Path | None) -> None:
        self.zed = zed.resolve()
        self.evidence = evidence.resolve()
        self.strace = strace.resolve() if strace else None
        self.invocations: list[Invocation] = []
        self.evidence.mkdir(parents=True, exist_ok=True)
        self._trace_index = 0

        if not self.zed.is_file():
            raise SystemExit(f"zed binary does not exist: {self.zed}")
        if not os.access(self.zed, os.X_OK):
            raise SystemExit(f"zed binary is not executable: {self.zed}")
        if self.strace is not None and not self.strace.is_file():
            raise SystemExit(f"strace path does not exist: {self.strace}")

    def run(
        self,
        project: Path,
        args: list[str],
        env: dict[str, str],
        *,
        expect_success: bool,
        diagnostic: str | None = None,
        audit: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        argv = [str(self.zed), *args]
        trace_file: Path | None = None
        command = argv
        if audit:
            if self.strace is None:
                raise AssertionError("an audited invocation requires strace")
            self._trace_index += 1
            trace_file = self.evidence / f"strace-{self._trace_index:02d}.log"
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
            cwd=project,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
        self.invocations.append(
            Invocation(
                argv=argv,
                cwd=str(project),
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                audited=audit,
                trace_file=str(trace_file) if trace_file else None,
            )
        )

        if expect_success and result.returncode != 0:
            raise AssertionError(
                f"command failed with {result.returncode}: {' '.join(argv)}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        if not expect_success and result.returncode == 0:
            raise AssertionError(f"command unexpectedly succeeded: {' '.join(argv)}")
        if diagnostic is not None:
            combined = f"{result.stdout}\n{result.stderr}"
            if diagnostic not in combined:
                raise AssertionError(
                    f"expected diagnostic {diagnostic!r} from {' '.join(argv)}; got:\n{combined}"
                )
        if trace_file is not None:
            self._assert_no_runtime_network_or_credential_reads(trace_file, env)
        return result

    @staticmethod
    def _assert_no_runtime_network_or_credential_reads(
        trace_file: Path, env: dict[str, str]
    ) -> None:
        trace = trace_file.read_text(encoding="utf-8", errors="replace")
        network_calls = [
            line
            for line in trace.splitlines()
            if re.search(
                r"\b(socket|socketpair|connect|accept|accept4|bind|listen|sendto|recvfrom|"
                r"sendmsg|recvmsg|getpeername|getsockname|shutdown)\(",
                line,
            )
        ]
        if network_calls:
            raise AssertionError(
                "credential-free OCI planning made runtime network syscalls:\n"
                + "\n".join(network_calls[:40])
            )

        zed_home = Path(env["ZED_PKG_HOME"])
        forbidden = [
            zed_home / "credentials.toml",
            zed_home / "auth" / "sessions.toml",
        ]
        opened = [str(path) for path in forbidden if str(path) in trace]
        if opened:
            raise AssertionError(
                "credential-free OCI planning opened credential/session files: "
                + ", ".join(opened)
            )

    def write_evidence(self, summary: dict[str, Any]) -> None:
        payload = {
            "schema": "zed-pkg-test.oci-plan-evidence/v1",
            "summary": summary,
            "invocations": [invocation.__dict__ for invocation in self.invocations],
        }
        (self.evidence / "evidence.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", type=Path, required=True)
    parser.add_argument("--node-lib", type=Path, required=True)
    parser.add_argument("--node-app", type=Path, required=True)
    parser.add_argument("--polyglot-lib", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--strace", type=Path, required=True)
    return parser.parse_args()


def copy_fixture(source: Path, destination: Path) -> Path:
    source = source.resolve()
    if not (source / ".zpkg.toml").is_file():
        raise AssertionError(f"fixture has no .zpkg.toml: {source}")
    shutil.copytree(
        source,
        destination,
        symlinks=True,
        ignore=shutil.ignore_patterns(".git"),
    )
    return destination


def tree_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(oct(stat.S_IMODE(metadata.st_mode)).encode("ascii"))
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(b"symlink\0")
            digest.update(os.readlink(path).encode("utf-8"))
        elif path.is_file():
            digest.update(b"file\0")
            digest.update(path.read_bytes())
        elif path.is_dir():
            digest.update(b"dir\0")
        else:
            raise AssertionError(f"unsupported fixture filesystem entry: {path}")
        digest.update(b"\0")
    return digest.hexdigest()


def poison_runtime_state(work: Path) -> tuple[dict[str, str], Path]:
    home = work / "home"
    zed_home = home / ".zed-pkg"
    auth = zed_home / "auth"
    auth.mkdir(parents=True)

    credentials = zed_home / "credentials.toml"
    sessions = auth / "sessions.toml"
    credentials.write_text("this is deliberately invalid TOML = [\n", encoding="utf-8")
    sessions.write_text("this is deliberately invalid TOML = [\n", encoding="utf-8")
    credentials.chmod(0)
    sessions.chmod(0)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "ZED_PKG_HOME": str(zed_home),
            "ZED_PKG_REGISTRY": "http://127.0.0.1:9",
            "ZED_PKG_AUTH_URL": "http://127.0.0.1:9/shared-auth",
            "GIT_TERMINAL_PROMPT": "0",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "ALL_PROXY": "http://127.0.0.1:9",
            "http_proxy": "http://127.0.0.1:9",
            "https_proxy": "http://127.0.0.1:9",
            "all_proxy": "http://127.0.0.1:9",
            "NO_PROXY": "",
            "no_proxy": "",
        }
    )
    env.pop("ZED_PKG_TOKEN", None)
    env.pop("ZED_PKG_AUTH_PASSWORD", None)
    return env, zed_home


def add_unrecoverable_transaction_sentinel(project: Path) -> Path:
    sentinel = project / ".zpkg-staging" / "unrecoverable-contract-sentinel"
    sentinel.mkdir(parents=True)
    marker = sentinel / "must-remain.txt"
    marker.write_text(
        "zed oci plan must not invoke project transaction recovery\n",
        encoding="utf-8",
    )
    return marker


def invoke_plan(
    harness: ContractHarness,
    project: Path,
    destination: str,
    env: dict[str, str],
    *,
    target: str | None = None,
    audit: bool = False,
) -> tuple[str, dict[str, Any]]:
    args = ["oci", "plan", destination]
    if target is not None:
        args.extend(["--target", target])
    args.append("--json")

    before = tree_fingerprint(project)
    result = harness.run(
        project,
        args,
        env,
        expect_success=True,
        audit=audit,
    )
    after = tree_fingerprint(project)
    if before != after:
        raise AssertionError(f"OCI planning mutated project tree: {project}")
    if (project / ".zed" / "pack").exists():
        raise AssertionError("OCI planning left persistent .zed/pack output")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(f"OCI plan stdout was not JSON: {error}\n{result.stdout}") from error
    return result.stdout, payload


def invoke_failure(
    harness: ContractHarness,
    project: Path,
    destination: str,
    env: dict[str, str],
    diagnostic: str,
    *,
    target: str | None = None,
) -> None:
    args = ["oci", "plan", destination]
    if target is not None:
        args.extend(["--target", target])
    args.append("--json")
    before = tree_fingerprint(project)
    harness.run(
        project,
        args,
        env,
        expect_success=False,
        diagnostic=diagnostic,
    )
    after = tree_fingerprint(project)
    if before != after:
        raise AssertionError(f"failed OCI planning mutated project tree: {project}")


def assert_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not DIGEST_RE.fullmatch(value):
        raise AssertionError(f"{label} is not a canonical sha256 OCI digest: {value!r}")
    return value


def validate_plan(
    payload: dict[str, Any],
    *,
    org: str,
    name: str,
    version: str,
    repository: str,
    target: str | None,
    expect_lock: bool,
) -> str:
    if payload.get("schema") != "zed.oci-publish-plan/v1":
        raise AssertionError(f"unexpected plan schema: {payload.get('schema')!r}")

    expected_package: dict[str, Any] = {
        "org": org,
        "name": name,
        "version": version,
    }
    if target is not None:
        expected_package["target"] = target
    if payload.get("package") != expected_package:
        raise AssertionError(
            f"package identity mismatch: {payload.get('package')!r} != {expected_package!r}"
        )

    requested = payload.get("requested_destination")
    if requested != {
        "registry": "ghcr.io",
        "repository": repository,
        "tag": version,
    }:
        raise AssertionError(f"unexpected requested destination: {requested!r}")

    resolved = payload.get("resolved_reference")
    if not isinstance(resolved, dict):
        raise AssertionError("resolved_reference must be an object")
    if resolved.get("registry") != "ghcr.io":
        raise AssertionError(f"unexpected resolved registry: {resolved!r}")
    if resolved.get("repository") != repository or resolved.get("tag") != version:
        raise AssertionError(f"resolved reference changed repository/tag: {resolved!r}")
    manifest_digest = assert_digest(resolved.get("digest"), "resolved reference")

    adapter = payload.get("adapter")
    if not isinstance(adapter, dict):
        raise AssertionError("adapter must be an object")
    if adapter.get("schema") != "zed.oci-adapter/v1":
        raise AssertionError(f"unexpected adapter schema: {adapter.get('schema')!r}")
    if adapter.get("package") != expected_package:
        raise AssertionError("adapter package identity diverged from plan identity")
    if adapter.get("reference") != resolved:
        raise AssertionError("adapter reference diverged from resolved reference")

    manifest = adapter.get("manifest")
    config = adapter.get("config")
    if not isinstance(manifest, dict) or not isinstance(config, dict):
        raise AssertionError("adapter manifest/config descriptors are required")
    if manifest.get("mediaType") != OCI_MANIFEST_MEDIA_TYPE:
        raise AssertionError(f"unexpected OCI manifest media type: {manifest!r}")
    if assert_digest(manifest.get("digest"), "OCI manifest descriptor") != manifest_digest:
        raise AssertionError("resolved digest does not equal OCI manifest descriptor digest")
    if not isinstance(manifest.get("size"), int) or manifest["size"] <= 0:
        raise AssertionError("OCI manifest descriptor size must be positive")
    if config.get("mediaType") != ZED_CONFIG_MEDIA_TYPE:
        raise AssertionError(f"unexpected Zed config media type: {config!r}")
    assert_digest(config.get("digest"), "Zed config descriptor")
    if not isinstance(config.get("size"), int) or config["size"] <= 0:
        raise AssertionError("Zed config descriptor size must be positive")

    layers = adapter.get("layers")
    if not isinstance(layers, list) or not layers:
        raise AssertionError("adapter layers must be a non-empty array")
    layer_by_kind: dict[str, dict[str, Any]] = {}
    descriptor_digests: set[str] = set()
    for layer in layers:
        if not isinstance(layer, dict) or not isinstance(layer.get("kind"), str):
            raise AssertionError(f"invalid typed layer: {layer!r}")
        kind = layer["kind"]
        if kind in layer_by_kind:
            raise AssertionError(f"duplicate layer kind in fixture plan: {kind}")
        descriptor = layer.get("descriptor")
        if not isinstance(descriptor, dict):
            raise AssertionError(f"layer {kind} has no descriptor")
        digest = assert_digest(descriptor.get("digest"), f"{kind} layer")
        if digest in descriptor_digests:
            raise AssertionError(f"duplicate layer digest: {digest}")
        descriptor_digests.add(digest)
        if not isinstance(descriptor.get("size"), int) or descriptor["size"] <= 0:
            raise AssertionError(f"layer {kind} has a non-positive size")
        layer_by_kind[kind] = layer

    expected_layer_types = {
        "package-tar-gz": PACKAGE_MEDIA_TYPE,
        "manifest": MANIFEST_MEDIA_TYPE,
    }
    if expect_lock:
        expected_layer_types["lockfile"] = LOCK_MEDIA_TYPE
    if set(layer_by_kind) != set(expected_layer_types):
        raise AssertionError(
            f"unexpected layer kinds: {sorted(layer_by_kind)}; expected {sorted(expected_layer_types)}"
        )
    for kind, media_type in expected_layer_types.items():
        actual = layer_by_kind[kind]["descriptor"].get("mediaType")
        if actual != media_type:
            raise AssertionError(f"layer {kind} media type {actual!r} != {media_type!r}")

    blobs = payload.get("blobs")
    if not isinstance(blobs, list):
        raise AssertionError("planned blobs must be an array")
    blob_by_kind: dict[str, dict[str, Any]] = {}
    for blob in blobs:
        if not isinstance(blob, dict) or not isinstance(blob.get("kind"), str):
            raise AssertionError(f"invalid planned blob: {blob!r}")
        kind = blob["kind"]
        if kind in blob_by_kind:
            raise AssertionError(f"duplicate planned blob kind: {kind}")
        assert_digest(blob.get("digest"), f"{kind} planned blob")
        if not isinstance(blob.get("size"), int) or blob["size"] <= 0:
            raise AssertionError(f"planned blob {kind} has a non-positive size")
        if not isinstance(blob.get("source"), str) or not blob["source"]:
            raise AssertionError(f"planned blob {kind} has no source label")
        blob_by_kind[kind] = blob

    expected_blobs = {"config", "package", "manifest", "oci-manifest"}
    if expect_lock:
        expected_blobs.add("lockfile")
    if set(blob_by_kind) != expected_blobs:
        raise AssertionError(
            f"unexpected planned blob kinds: {sorted(blob_by_kind)}; expected {sorted(expected_blobs)}"
        )
    if blob_by_kind["oci-manifest"]["digest"] != manifest_digest:
        raise AssertionError("OCI manifest blob digest diverged from resolved reference")
    if blob_by_kind["config"]["digest"] != config["digest"]:
        raise AssertionError("config blob digest diverged from config descriptor")
    if (
        blob_by_kind["package"]["digest"]
        != layer_by_kind["package-tar-gz"]["descriptor"]["digest"]
    ):
        raise AssertionError("package blob digest diverged from package layer")
    if (
        blob_by_kind["manifest"]["digest"]
        != layer_by_kind["manifest"]["descriptor"]["digest"]
    ):
        raise AssertionError("manifest blob digest diverged from manifest layer")
    if expect_lock and (
        blob_by_kind["lockfile"]["digest"]
        != layer_by_kind["lockfile"]["descriptor"]["digest"]
    ):
        raise AssertionError("lockfile blob digest diverged from lockfile layer")

    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def deterministic_plan(
    harness: ContractHarness,
    project: Path,
    destination: str,
    env: dict[str, str],
    *,
    target: str | None = None,
    audit_first: bool = False,
) -> dict[str, Any]:
    first_text, first = invoke_plan(
        harness,
        project,
        destination,
        env,
        target=target,
        audit=audit_first,
    )
    second_text, second = invoke_plan(
        harness,
        project,
        destination,
        env,
        target=target,
    )
    if first_text != second_text or first != second:
        raise AssertionError(
            f"OCI plan is not byte-deterministic for {destination} target={target!r}"
        )
    return first


def write_lock(path: Path, version: str, sha256: str) -> None:
    if not HEX_SHA256_RE.fullmatch(sha256):
        raise AssertionError("test lock digest must be 64 lowercase hexadecimal characters")
    path.write_text(
        "\n".join(
            [
                "version = 1",
                "",
                "[[package]]",
                'org = "zed-pkg-test"',
                'name = "node-lib"',
                f'version = "{version}"',
                f'sha256 = "{sha256}"',
                "size = 10",
                'vcs_tag = "v1.0.0"',
                'vcs_commit = "2d41e658a382fdff27ef87a0db24dbaff280a685"',
                'source = "file:///contract-registry"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    summary: dict[str, Any] = {"plans": {}, "negative_cases": []}
    harness = ContractHarness(args.zed, args.evidence, args.strace)

    try:
        with tempfile.TemporaryDirectory(prefix="zed-oci-plan-contract-") as temporary:
            work = Path(temporary)
            env, _zed_home = poison_runtime_state(work)

            node_lib = copy_fixture(args.node_lib, work / "node-lib")
            node_sentinel = add_unrecoverable_transaction_sentinel(node_lib)
            node_plan = deterministic_plan(
                harness,
                node_lib,
                "oci://ghcr.io/zed-pkg-test/node-lib:1.0.0",
                env,
                audit_first=True,
            )
            summary["plans"]["node-lib"] = validate_plan(
                node_plan,
                org="zed-pkg-test",
                name="node-lib",
                version="1.0.0",
                repository="zed-pkg-test/node-lib",
                target=None,
                expect_lock=False,
            )
            if not node_sentinel.is_file():
                raise AssertionError("OCI planning invoked transaction recovery")

            invoke_failure(
                harness,
                node_lib,
                "oci://ghcr.io/zed-pkg-test/node-lib:latest",
                env,
                "must equal package version",
            )
            summary["negative_cases"].append("tag-drift")
            invoke_failure(
                harness,
                node_lib,
                "oci://ghcr.io/zed-pkg-test/node-lib:1.0.0@sha256:"
                + "a" * 64,
                env,
                "preselected digest",
            )
            summary["negative_cases"].append("caller-selected-digest")

            polyglot = copy_fixture(args.polyglot_lib, work / "polyglot-lib")
            poly_sentinel = add_unrecoverable_transaction_sentinel(polyglot)
            invoke_failure(
                harness,
                polyglot,
                "oci://ghcr.io/zedtest/polyglot-lib:0.1.0",
                env,
                "requires --target",
            )
            summary["negative_cases"].append("polyglot-target-required")
            for target in ("nodejs", "python", "golang", "rust"):
                name = f"polyglot-lib-{target}"
                payload = deterministic_plan(
                    harness,
                    polyglot,
                    f"oci://ghcr.io/zedtest/{name}:0.1.0",
                    env,
                    target=target,
                )
                summary["plans"][name] = validate_plan(
                    payload,
                    org="zedtest",
                    name=name,
                    version="0.1.0",
                    repository=f"zedtest/{name}",
                    target=target,
                    expect_lock=False,
                )
            if not poly_sentinel.is_file():
                raise AssertionError("polyglot planning invoked transaction recovery")

            node_app = copy_fixture(args.node_app, work / "node-app")
            app_sentinel = add_unrecoverable_transaction_sentinel(node_app)
            invoke_failure(
                harness,
                node_app,
                "oci://ghcr.io/zed-pkg-test/node-app:0.1.0",
                env,
                ".zpkg.lock is required",
            )
            summary["negative_cases"].append("dependency-lock-required")

            lock = node_app / ".zpkg.lock"
            write_lock(lock, "1.0.0", "a" * 64)
            app_plan = deterministic_plan(
                harness,
                node_app,
                "oci://ghcr.io/zed-pkg-test/node-app:0.1.0",
                env,
            )
            summary["plans"]["node-app"] = validate_plan(
                app_plan,
                org="zed-pkg-test",
                name="node-app",
                version="0.1.0",
                repository="zed-pkg-test/node-app",
                target=None,
                expect_lock=True,
            )

            write_lock(lock, "2.0.0", "a" * 64)
            invoke_failure(
                harness,
                node_app,
                "oci://ghcr.io/zed-pkg-test/node-app:0.1.0",
                env,
                "lock drift",
            )
            summary["negative_cases"].append("dependency-version-drift")

            write_lock(lock, "1.0.0", "A" * 64.lower())
            # The expression above intentionally remains lowercase in Python;
            # replace the bytes directly to exercise uppercase digest rejection.
            lock.write_text(
                lock.read_text(encoding="utf-8").replace("a" * 64, "A" * 64),
                encoding="utf-8",
            )
            invoke_failure(
                harness,
                node_app,
                "oci://ghcr.io/zed-pkg-test/node-app:0.1.0",
                env,
                "lowercase sha256",
            )
            summary["negative_cases"].append("noncanonical-lock-digest")
            if not app_sentinel.is_file():
                raise AssertionError("dependency planning invoked transaction recovery")

            summary["plan_count"] = len(summary["plans"])
            summary["negative_case_count"] = len(summary["negative_cases"])
            summary["invocation_count"] = len(harness.invocations)
            if summary["plan_count"] != 6:
                raise AssertionError(f"unexpected plan count: {summary['plan_count']}")
            if summary["negative_case_count"] != 6:
                raise AssertionError(
                    f"unexpected negative-case count: {summary['negative_case_count']}"
                )
    finally:
        harness.write_evidence(summary)

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
