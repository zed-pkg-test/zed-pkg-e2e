#!/usr/bin/env python3
"""Black-box certification for `zed oci plan`."""

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
from pathlib import Path
from typing import Any

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
NETWORK_RE = re.compile(
    r"\b(socket|socketpair|connect|accept|accept4|bind|listen|sendto|recvfrom|"
    r"sendmsg|recvmsg|getpeername|getsockname|shutdown)\("
)
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
ZED_CONFIG = "application/vnd.zed.package.config.v1+json"
PACKAGE_LAYER = "application/vnd.zed.package.v1.tar+gzip"
MANIFEST_LAYER = "application/vnd.zed.package.manifest.v1+toml"
LOCK_LAYER = "application/vnd.zed.package.lock.v1+toml"


class Harness:
    def __init__(self, zed: Path, evidence: Path, strace: Path) -> None:
        self.zed = zed.resolve()
        self.evidence = evidence.resolve()
        self.strace = strace.resolve()
        self.records: list[dict[str, Any]] = []
        self.trace_index = 0
        self.evidence.mkdir(parents=True, exist_ok=True)
        for label, path in (("zed", self.zed), ("strace", self.strace)):
            if not path.is_file():
                raise AssertionError(f"{label} executable does not exist: {path}")
            if not os.access(path, os.X_OK):
                raise AssertionError(f"{label} is not executable: {path}")

    def invoke(
        self,
        project: Path,
        args: list[str],
        env: dict[str, str],
        *,
        success: bool,
        diagnostic: str | None = None,
        audit: bool = False,
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
            cwd=project,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
        self.records.append(
            {
                "argv": argv,
                "cwd": str(project),
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
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
        if diagnostic is not None and diagnostic not in result.stdout + result.stderr:
            raise AssertionError(
                f"missing diagnostic {diagnostic!r}: {' '.join(argv)}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        if trace_file is not None:
            self.assert_credential_free(trace_file, env)
        return result

    @staticmethod
    def assert_credential_free(trace_file: Path, env: dict[str, str]) -> None:
        trace = trace_file.read_text(encoding="utf-8", errors="replace")
        network = [line for line in trace.splitlines() if NETWORK_RE.search(line)]
        if network:
            raise AssertionError(
                "OCI planning made runtime network syscalls:\n" + "\n".join(network[:40])
            )
        zed_home = Path(env["ZED_PKG_HOME"])
        forbidden = [zed_home / "credentials.toml", zed_home / "auth" / "sessions.toml"]
        opened = [str(path) for path in forbidden if str(path) in trace]
        if opened:
            raise AssertionError("OCI planning opened credential state: " + ", ".join(opened))

    def save(self, summary: dict[str, Any]) -> None:
        payload = {
            "schema": "zed-pkg-test.oci-plan-evidence/v1",
            "summary": summary,
            "invocations": self.records,
        }
        (self.evidence / "evidence.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", required=True, type=Path)
    parser.add_argument("--strace", required=True, type=Path)
    parser.add_argument("--node-lib", required=True, type=Path)
    parser.add_argument("--node-app", required=True, type=Path)
    parser.add_argument("--polyglot-lib", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    return parser.parse_args()


def copy_fixture(source: Path, destination: Path) -> Path:
    if not (source / ".zpkg.toml").is_file():
        raise AssertionError(f"fixture has no .zpkg.toml: {source}")
    shutil.copytree(
        source,
        destination,
        symlinks=True,
        ignore=shutil.ignore_patterns(".git"),
    )
    return destination


def fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        metadata = path.lstat()
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(oct(stat.S_IMODE(metadata.st_mode)).encode())
        if path.is_symlink():
            digest.update(b"symlink")
            digest.update(os.readlink(path).encode())
        elif path.is_file():
            digest.update(b"file")
            digest.update(path.read_bytes())
        elif path.is_dir():
            digest.update(b"dir")
        else:
            raise AssertionError(f"unsupported fixture entry: {path}")
        digest.update(b"\0")
    return digest.hexdigest()


def poisoned_environment(work: Path) -> dict[str, str]:
    home = work / "home"
    zed_home = home / ".zed-pkg"
    auth = zed_home / "auth"
    auth.mkdir(parents=True)
    for path in (zed_home / "credentials.toml", auth / "sessions.toml"):
        path.write_text("deliberately invalid TOML = [\n", encoding="utf-8")
        path.chmod(0)

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
    for key in ("ZED_PKG_TOKEN", "ZED_PKG_AUTH_PASSWORD"):
        env.pop(key, None)
    return env


def transaction_sentinel(project: Path) -> Path:
    marker = project / ".zpkg-staging" / "unrecoverable-contract-sentinel" / "must-remain.txt"
    marker.parent.mkdir(parents=True)
    marker.write_text("OCI planning must not recover project transactions\n", encoding="utf-8")
    return marker


def command(destination: str, target: str | None = None) -> list[str]:
    result = ["oci", "plan", destination]
    if target is not None:
        result.extend(["--target", target])
    result.append("--json")
    return result


def successful_plan(
    harness: Harness,
    project: Path,
    destination: str,
    env: dict[str, str],
    *,
    target: str | None = None,
    audit: bool = False,
) -> tuple[str, dict[str, Any]]:
    before = fingerprint(project)
    result = harness.invoke(
        project,
        command(destination, target),
        env,
        success=True,
        audit=audit,
    )
    if before != fingerprint(project):
        raise AssertionError(f"OCI planning mutated project tree: {project}")
    if (project / ".zed" / "pack").exists():
        raise AssertionError("OCI planning left persistent .zed/pack output")
    try:
        return result.stdout, json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(f"OCI plan stdout is not JSON: {result.stdout}") from error


def failed_plan(
    harness: Harness,
    project: Path,
    destination: str,
    env: dict[str, str],
    diagnostic: str,
    *,
    target: str | None = None,
) -> None:
    before = fingerprint(project)
    harness.invoke(
        project,
        command(destination, target),
        env,
        success=False,
        diagnostic=diagnostic,
    )
    if before != fingerprint(project):
        raise AssertionError(f"failed OCI planning mutated project tree: {project}")


def deterministic_plan(
    harness: Harness,
    project: Path,
    destination: str,
    env: dict[str, str],
    *,
    target: str | None = None,
    audit_first: bool = False,
) -> dict[str, Any]:
    first_text, first = successful_plan(
        harness,
        project,
        destination,
        env,
        target=target,
        audit=audit_first,
    )
    second_text, second = successful_plan(
        harness,
        project,
        destination,
        env,
        target=target,
    )
    if first_text != second_text or first != second:
        raise AssertionError(f"OCI plan is not deterministic: {destination} target={target}")
    return first


def digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or DIGEST_RE.fullmatch(value) is None:
        raise AssertionError(f"{label} is not a canonical OCI SHA-256 digest: {value!r}")
    return value


def validate(
    payload: dict[str, Any],
    *,
    org: str,
    name: str,
    version: str,
    repository: str,
    target: str | None,
    has_lock: bool,
) -> str:
    expected_package: dict[str, Any] = {"org": org, "name": name, "version": version}
    if target is not None:
        expected_package["target"] = target
    assert payload["schema"] == "zed.oci-publish-plan/v1"
    assert payload["package"] == expected_package
    assert payload["requested_destination"] == {
        "registry": "ghcr.io",
        "repository": repository,
        "tag": version,
    }

    resolved = payload["resolved_reference"]
    assert resolved["registry"] == "ghcr.io"
    assert resolved["repository"] == repository
    assert resolved["tag"] == version
    manifest_digest = digest(resolved["digest"], "resolved reference")

    adapter = payload["adapter"]
    assert adapter["schema"] == "zed.oci-adapter/v1"
    assert adapter["package"] == expected_package
    assert adapter["reference"] == resolved
    assert adapter["manifest"]["mediaType"] == OCI_MANIFEST
    assert digest(adapter["manifest"]["digest"], "manifest descriptor") == manifest_digest
    assert adapter["manifest"]["size"] > 0
    assert adapter["config"]["mediaType"] == ZED_CONFIG
    digest(adapter["config"]["digest"], "config descriptor")
    assert adapter["config"]["size"] > 0

    layers = {layer["kind"]: layer["descriptor"] for layer in adapter["layers"]}
    expected_layers = {
        "package-tar-gz": PACKAGE_LAYER,
        "manifest": MANIFEST_LAYER,
    }
    if has_lock:
        expected_layers["lockfile"] = LOCK_LAYER
    assert set(layers) == set(expected_layers)
    layer_digests: set[str] = set()
    for kind, media_type in expected_layers.items():
        descriptor = layers[kind]
        assert descriptor["mediaType"] == media_type
        layer_digest = digest(descriptor["digest"], f"{kind} layer")
        assert layer_digest not in layer_digests
        layer_digests.add(layer_digest)
        assert descriptor["size"] > 0

    blobs = {blob["kind"]: blob for blob in payload["blobs"]}
    expected_blobs = {"config", "package", "manifest", "oci-manifest"}
    if has_lock:
        expected_blobs.add("lockfile")
    assert set(blobs) == expected_blobs
    for kind, blob in blobs.items():
        digest(blob["digest"], f"{kind} blob")
        assert blob["size"] > 0
        assert blob["source"]
    assert blobs["oci-manifest"]["digest"] == manifest_digest
    assert blobs["config"]["digest"] == adapter["config"]["digest"]
    assert blobs["package"]["digest"] == layers["package-tar-gz"]["digest"]
    assert blobs["manifest"]["digest"] == layers["manifest"]["digest"]
    if has_lock:
        assert blobs["lockfile"]["digest"] == layers["lockfile"]["digest"]

    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(canonical).hexdigest()


def write_lock(path: Path, version: str, sha256: str) -> None:
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
    args = arguments()
    harness = Harness(args.zed, args.evidence, args.strace)
    summary: dict[str, Any] = {"plans": {}, "negative_cases": []}
    try:
        with tempfile.TemporaryDirectory(prefix="zed-oci-plan-contract-") as temporary:
            work = Path(temporary)
            env = poisoned_environment(work)

            node = copy_fixture(args.node_lib, work / "node-lib")
            node_marker = transaction_sentinel(node)
            payload = deterministic_plan(
                harness,
                node,
                "oci://ghcr.io/zed-pkg-test/node-lib:1.0.0",
                env,
                audit_first=True,
            )
            summary["plans"]["node-lib"] = validate(
                payload,
                org="zed-pkg-test",
                name="node-lib",
                version="1.0.0",
                repository="zed-pkg-test/node-lib",
                target=None,
                has_lock=False,
            )
            assert node_marker.is_file()
            failed_plan(
                harness,
                node,
                "oci://ghcr.io/zed-pkg-test/node-lib:latest",
                env,
                "must equal package version",
            )
            summary["negative_cases"].append("tag-drift")
            failed_plan(
                harness,
                node,
                "oci://ghcr.io/zed-pkg-test/node-lib:1.0.0@sha256:" + "a" * 64,
                env,
                "preselected digest",
            )
            summary["negative_cases"].append("caller-selected-digest")

            poly = copy_fixture(args.polyglot_lib, work / "polyglot-lib")
            poly_marker = transaction_sentinel(poly)
            failed_plan(
                harness,
                poly,
                "oci://ghcr.io/zedtest/polyglot-lib:0.1.0",
                env,
                "requires --target",
            )
            summary["negative_cases"].append("polyglot-target-required")
            for target in ("nodejs", "python", "golang", "rust"):
                name = f"polyglot-lib-{target}"
                payload = deterministic_plan(
                    harness,
                    poly,
                    f"oci://ghcr.io/zedtest/{name}:0.1.0",
                    env,
                    target=target,
                )
                summary["plans"][name] = validate(
                    payload,
                    org="zedtest",
                    name=name,
                    version="0.1.0",
                    repository=f"zedtest/{name}",
                    target=target,
                    has_lock=False,
                )
            assert poly_marker.is_file()

            app = copy_fixture(args.node_app, work / "node-app")
            app_marker = transaction_sentinel(app)
            failed_plan(
                harness,
                app,
                "oci://ghcr.io/zed-pkg-test/node-app:0.1.0",
                env,
                ".zpkg.lock is required",
            )
            summary["negative_cases"].append("dependency-lock-required")

            lock = app / ".zpkg.lock"
            write_lock(lock, "1.0.0", "a" * 64)
            payload = deterministic_plan(
                harness,
                app,
                "oci://ghcr.io/zed-pkg-test/node-app:0.1.0",
                env,
            )
            summary["plans"]["node-app"] = validate(
                payload,
                org="zed-pkg-test",
                name="node-app",
                version="0.1.0",
                repository="zed-pkg-test/node-app",
                target=None,
                has_lock=True,
            )

            write_lock(lock, "2.0.0", "a" * 64)
            failed_plan(
                harness,
                app,
                "oci://ghcr.io/zed-pkg-test/node-app:0.1.0",
                env,
                "lock drift",
            )
            summary["negative_cases"].append("dependency-version-drift")

            write_lock(lock, "1.0.0", "A" * 64)
            failed_plan(
                harness,
                app,
                "oci://ghcr.io/zed-pkg-test/node-app:0.1.0",
                env,
                "expected 64 lowercase hex chars",
            )
            summary["negative_cases"].append("noncanonical-lock-digest")
            assert app_marker.is_file()

            summary["plan_count"] = len(summary["plans"])
            summary["negative_case_count"] = len(summary["negative_cases"])
            summary["invocation_count"] = len(harness.records)
            assert summary["plan_count"] == 6
            assert summary["negative_case_count"] == 6
    finally:
        harness.save(summary)

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
