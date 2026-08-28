#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tomllib
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
PIN = "32be546f5ee020c1de3b099a47e6760d00e3f6e4"


class CertificationError(RuntimeError):
    pass


def executable_name(stem: str) -> str:
    return f"{stem}.exe" if os.name == "nt" else stem


def copy_executable(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if os.name != "nt":
        destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def sanitized_environment(home: Path, path_value: str | None = None) -> dict[str, str]:
    environment = dict(os.environ)
    for key in list(environment):
        upper = key.upper()
        if any(fragment in upper for fragment in SENSITIVE_ENV_FRAGMENTS):
            environment.pop(key, None)
        elif upper.startswith(("ZED_PKG_", "ZED_EXTERNAL_", "ZED_PROBE_")):
            environment.pop(key, None)
    environment["HOME"] = str(home)
    environment["USERPROFILE"] = str(home)
    environment["CI"] = "true"
    if path_value is not None:
        environment["PATH"] = path_value
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
        timeout=45,
        check=False,
    )
    if result.returncode not in expected_codes:
        rendered = " ".join(repr(str(item)) for item in command)
        raise CertificationError(
            f"command returned {result.returncode}, expected {sorted(expected_codes)}: {rendered}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CertificationError(message)


def combined(result: subprocess.CompletedProcess[str]) -> str:
    return f"{result.stdout}\n{result.stderr}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_catalog_fixture(root: Path, environment: dict[str, str]) -> Path:
    catalog = root / "catalog" / "gitops" / "apps"
    static_apps = root / "remote" / "argocd" / "apps"
    catalog.mkdir(parents=True)
    static_apps.mkdir(parents=True)
    (static_apps / "daedalus.applications.yaml").write_text(
        "kind: Application\n", encoding="utf-8"
    )
    (root / ".gitmodules").write_text(
        '[submodule "remote/deployments/fabrication-server-rs"]\n'
        "\tpath = remote/deployments/fabrication-server-rs\n"
        "\turl = git@github.com:daedalus-fab/fabrication-server.rs.git\n",
        encoding="utf-8",
    )
    run(["git", "-C", root, "init", "--quiet"], environment=environment)
    run(
        [
            "git",
            "-C",
            root,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{PIN},remote/deployments/fabrication-server-rs",
        ],
        environment=environment,
    )
    record = {
        "$schema": "../application.schema.json",
        "apiVersion": "oresoftware.dev/v1alpha1",
        "kind": "GitOpsApplication",
        "metadata": {"name": "dd-fabrication-server"},
        "spec": {
            "owner": "daedalus-fab",
            "inventory": {
                "mode": "git-submodule",
                "path": "remote/deployments/fabrication-server-rs",
                "repository": "git@github.com:daedalus-fab/fabrication-server.rs.git",
                "revision": PIN,
            },
            "source": {
                "mode": "direct-repository",
                "repository": "https://github.com/daedalus-fab/fabrication-server.rs",
                "targetRevision": PIN,
                "path": "k8s",
                "renderer": "kustomize",
            },
            "argo": {
                "project": "daedalus",
                "namespace": "daedalus",
                "destinationServer": "https://kubernetes.default.svc",
                "automated": False,
                "prune": False,
                "selfHeal": False,
            },
            "migration": {
                "phase": "pilot-inert",
                "staticApplication": "remote/argocd/apps/daedalus.applications.yaml",
            },
        },
    }
    record_path = catalog / "dd-fabrication-server.json"
    record_path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return record_path


def validate_package_manifest(product: Path) -> None:
    manifest = tomllib.loads((product / ".zpkg.toml").read_text(encoding="utf-8"))
    outputs = set(manifest.get("build", {}).get("outputs", []))
    require(
        outputs == {"target/release/zed", "target/release/zed-gitops"},
        f"unexpected packaged outputs: {sorted(outputs)}",
    )
    binaries = manifest.get("bin", {})
    require(
        binaries.get("zed") == "target/release/zed"
        and binaries.get("zed-gitops") == "target/release/zed-gitops",
        "Zed package must install both sibling executables",
    )
    smoke = manifest.get("publish", {}).get("smoke_test", "")
    require(
        "zed-gitops" in smoke and "gitops validate --help" in smoke,
        "publish smoke test must exercise root dispatch and the sibling validator",
    )


def certify(args: argparse.Namespace) -> dict[str, object]:
    candidate = args.candidate
    require(
        re.fullmatch(r"[0-9a-f]{40}", candidate) is not None,
        "candidate must be an exact lowercase 40-character commit",
    )

    zed = args.zed.resolve(strict=True)
    zed_gitops = args.zed_gitops.resolve(strict=True)
    probe = args.probe.resolve(strict=True)
    product = args.product.resolve(strict=True)
    work = args.work.resolve()
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    home = work / "home"
    home.mkdir()
    original_path = os.environ.get("PATH", "")
    base_environment = sanitized_environment(home, original_path)
    checks: list[str] = []

    validate_package_manifest(product)
    checks.append("package-installs-both-sibling-binaries")

    direct_help = run(
        [zed_gitops, "validate", "--help"], environment=base_environment
    )
    require("Usage: zed-gitops validate" in combined(direct_help), combined(direct_help))
    checks.append("standalone-validator-help")

    sibling = work / "sibling"
    sibling_zed = sibling / executable_name("zed")
    sibling_gitops = sibling / executable_name("zed-gitops")
    copy_executable(zed, sibling_zed)
    copy_executable(zed_gitops, sibling_gitops)

    root_help = run([sibling_zed, "--help"], environment=base_environment)
    root_help_text = combined(root_help)
    require("gitops" in root_help_text, "root help omitted gitops")
    require(
        "Validate GitOps composition" in root_help_text,
        "root help omitted the GitOps contract summary",
    )
    checks.append("root-help-advertises-gitops")

    routed_help = run(
        [sibling_zed, "gitops", "validate", "--help"],
        environment=base_environment,
    )
    routed_help_text = combined(routed_help)
    require("Usage: zed-gitops validate" in routed_help_text, routed_help_text)
    require("--offline" in routed_help_text, routed_help_text)
    checks.append("root-dispatches-to-sibling-validator")

    alias_help = run(
        [sibling_zed, "help", "gitops"], environment=base_environment
    )
    require("Usage: zed-gitops" in combined(alias_help), combined(alias_help))
    checks.append("help-alias-reaches-validator")

    completion = run(
        [sibling_zed, "completions", "bash"], environment=base_environment
    )
    require(
        "gitops" in completion.stdout and "validate" in completion.stdout,
        "Bash completion omitted gitops validate",
    )
    checks.append("completion-exposes-gitops-contract")

    fixture = work / "fixture"
    fixture.mkdir()
    record_path = build_catalog_fixture(fixture, base_environment)
    validate_args = [
        "validate",
        "--root",
        fixture,
        "--offline",
        "--strict",
        "--format",
        "json",
    ]
    direct = run([sibling_gitops, *validate_args], environment=base_environment)
    routed = run(
        [sibling_zed, "gitops", *validate_args], environment=base_environment
    )
    direct_report = json.loads(direct.stdout)
    routed_report = json.loads(routed.stdout)
    require(direct_report == routed_report, "root and standalone reports differ")
    require(
        routed_report.get("valid") is True
        and routed_report.get("records") == 1
        and routed_report.get("errors") == 0,
        f"unexpected valid report: {routed_report}",
    )
    checks.append("root-and-standalone-validation-parity")

    online = run(
        [
            sibling_zed,
            "gitops",
            "validate",
            "--root",
            fixture,
            "--strict",
            "--format",
            "json",
        ],
        environment=base_environment,
        expected=1,
    )
    require(
        "online validation is not implemented" in combined(online),
        "online-mode omission did not fail explicitly",
    )
    checks.append("online-mode-fails-explicitly")

    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["spec"]["source"]["targetRevision"] = "a" * 40
    record_path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    drift = run(
        [sibling_zed, "gitops", *validate_args],
        environment=base_environment,
        expected=2,
    )
    drift_report = json.loads(drift.stdout)
    rule_ids = {
        diagnostic.get("rule_id")
        for diagnostic in drift_report.get("diagnostics", [])
        if isinstance(diagnostic, dict)
    }
    require("source.pin-drift" in rule_ids, f"pin drift was not reported: {drift_report}")
    checks.append("policy-failure-exit-code-and-diagnostic")

    missing = work / "missing"
    missing_zed = missing / executable_name("zed")
    copy_executable(zed, missing_zed)
    empty_path = work / "empty-path"
    empty_path.mkdir()
    missing_environment = sanitized_environment(home, str(empty_path))
    missing_result = run(
        [missing_zed, "gitops", "validate", "--help"],
        environment=missing_environment,
        expected=1,
    )
    require(
        "requires `zed-gitops`" in combined(missing_result),
        "missing known extension did not fail with an actionable diagnostic",
    )
    checks.append("missing-known-extension-fails-closed")

    cwd_attack = work / "cwd-attack"
    cwd_attack.mkdir()
    cwd_probe = cwd_attack / executable_name("zed-gitops")
    copy_executable(probe, cwd_probe)
    cwd_marker = cwd_attack / "marker.json"
    cwd_environment = sanitized_environment(home, str(empty_path))
    cwd_environment["ZED_PROBE_OUTPUT"] = str(cwd_marker)
    cwd_environment["ZED_PROBE_EXIT"] = "73"
    cwd_result = run(
        [missing_zed, "gitops", "validate", "--help"],
        environment=cwd_environment,
        cwd=cwd_attack,
        expected=1,
    )
    require("requires `zed-gitops`" in combined(cwd_result), combined(cwd_result))
    require(not cwd_marker.exists(), "dispatcher executed zed-gitops from the working directory")
    checks.append("working-directory-is-never-searched")

    relative_root = work / "relative-path"
    relative_plugins = relative_root / "relative-bin"
    relative_plugins.mkdir(parents=True)
    relative_probe = relative_plugins / executable_name("zed-gitops")
    copy_executable(probe, relative_probe)
    relative_marker = relative_root / "marker.json"
    relative_environment = sanitized_environment(home, "relative-bin")
    relative_environment["ZED_PROBE_OUTPUT"] = str(relative_marker)
    relative_environment["ZED_PROBE_EXIT"] = "74"
    relative_result = run(
        [missing_zed, "gitops", "validate", "--help"],
        environment=relative_environment,
        cwd=relative_root,
        expected=1,
    )
    require("requires `zed-gitops`" in combined(relative_result), combined(relative_result))
    require(not relative_marker.exists(), "dispatcher searched a relative PATH entry")
    checks.append("relative-path-entry-is-rejected")

    path_plugins = work / "absolute-plugins"
    path_plugins.mkdir()
    path_probe = path_plugins / executable_name("zed-gitops")
    copy_executable(probe, path_probe)
    precedence_marker = work / "precedence-marker.json"
    precedence_environment = sanitized_environment(
        home, os.pathsep.join([str(path_plugins), original_path])
    )
    precedence_environment["ZED_PROBE_OUTPUT"] = str(precedence_marker)
    precedence_environment["ZED_PROBE_EXIT"] = "75"
    precedence = run(
        [sibling_zed, "gitops", "validate", "--help"],
        environment=precedence_environment,
    )
    require("Usage: zed-gitops validate" in combined(precedence), combined(precedence))
    require(not precedence_marker.exists(), "PATH extension shadowed the sibling validator")
    checks.append("sibling-validator-precedes-path")

    generic_probe = path_plugins / executable_name("zed-probe")
    copy_executable(probe, generic_probe)
    probe_output = work / "probe-output.json"
    probe_home = work / "home with spaces"
    probe_home.mkdir()
    probe_environment = sanitized_environment(
        home, os.pathsep.join([str(path_plugins), original_path])
    )
    probe_environment["ZED_PROBE_OUTPUT"] = str(probe_output)
    probe_environment["ZED_PROBE_EXIT"] = "37"
    literal_arguments = ["literal;$HOME", "two words", "--flag=value"]
    probe_result = run(
        [
            missing_zed,
            "--home",
            probe_home,
            "--git-submodules=false",
            "probe",
            *literal_arguments,
        ],
        environment=probe_environment,
        expected=37,
    )
    require(probe_result.stdout == "" and probe_result.stderr == "", combined(probe_result))
    probe_payload = json.loads(probe_output.read_text(encoding="utf-8"))
    require(probe_payload.get("args") == literal_arguments, f"arguments changed: {probe_payload}")
    expected_environment = {
        "ZED_EXTERNAL_SUBCOMMAND": "probe",
        "ZED_PKG_HOME": str(probe_home),
        "ZED_PKG_GIT_SUBMODULES": "false",
    }
    require(
        probe_payload.get("env") == expected_environment,
        f"root-option environment changed: {probe_payload}",
    )
    checks.append("arguments-environment-and-exit-code-are-preserved")

    collision_probe = path_plugins / executable_name("zed-install")
    copy_executable(probe, collision_probe)
    collision_marker = work / "collision-marker.json"
    collision_environment = sanitized_environment(
        home, os.pathsep.join([str(path_plugins), original_path])
    )
    collision_environment["ZED_PROBE_OUTPUT"] = str(collision_marker)
    collision_environment["ZED_PROBE_EXIT"] = "91"
    collision = run(
        [missing_zed, "install", "--help"], environment=collision_environment
    )
    require("Usage:" in combined(collision), combined(collision))
    require(not collision_marker.exists(), "external executable shadowed a built-in command")
    checks.append("built-in-command-cannot-be-shadowed")

    return {
        "$schema": "zed-pkg-test/gitops-dispatch-canary/v1",
        "candidate": candidate,
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "binaries": {
            "zedSha256": sha256(zed),
            "zedGitopsSha256": sha256(zed_gitops),
            "probeSha256": sha256(probe),
        },
        "result": "passed",
        "checkCount": len(checks),
        "checks": checks,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", type=Path, required=True)
    parser.add_argument("--zed-gitops", type=Path, required=True)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--product", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--work", type=Path, required=True)
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
        f"certified {evidence['checkCount']} GitOps dispatch checks "
        f"against {evidence['candidate']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
