#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Sequence


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


class CertificationError(RuntimeError):
    pass


def executable_name(stem: str) -> str:
    return f"{stem}.exe" if os.name == "nt" else stem


def copy_executable(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if os.name != "nt":
        destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def sanitized_environment(home: Path, path_value: str) -> dict[str, str]:
    environment = dict(os.environ)
    for key in list(environment):
        upper = key.upper()
        if any(fragment in upper for fragment in SENSITIVE_ENV_FRAGMENTS):
            environment.pop(key, None)
        elif upper.startswith(("ZED_PKG_", "ZED_EXTERNAL_", "ZED_PROBE_")):
            environment.pop(key, None)
    environment["HOME"] = str(home)
    environment["USERPROFILE"] = str(home)
    environment["PATH"] = path_value
    environment["CI"] = "true"
    return environment


def run(
    command: Sequence[object],
    *,
    environment: dict[str, str],
    expected: set[int] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(item) for item in command],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    if expected is not None and result.returncode not in expected:
        rendered = " ".join(repr(str(item)) for item in command)
        raise CertificationError(
            f"command returned {result.returncode}, expected {sorted(expected)}: {rendered}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CertificationError(message)


def invoke_probe(
    zed: Path,
    arguments: Sequence[object],
    *,
    environment: dict[str, str],
    marker: Path,
) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
    marker.unlink(missing_ok=True)
    child_environment = dict(environment)
    child_environment["ZED_PROBE_OUTPUT"] = str(marker)
    child_environment["ZED_PROBE_EXIT"] = "0"
    result = run([zed, "probe", *arguments], environment=child_environment, expected={0})
    require(marker.is_file(), "external probe did not produce evidence")
    payload = json.loads(marker.read_text(encoding="utf-8"))
    require(isinstance(payload, dict), "probe evidence must be a JSON object")
    return result, payload


def certify(args: argparse.Namespace) -> dict[str, object]:
    zed = args.zed.resolve(strict=True)
    probe = args.probe.resolve(strict=True)
    work = args.work.resolve()
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    home = work / "home"
    home.mkdir()
    plugins = work / "plugins"
    probe_executable = plugins / executable_name("zed-probe")
    copy_executable(probe, probe_executable)
    original_path = os.environ.get("PATH", "")
    environment = sanitized_environment(
        home, os.pathsep.join([str(plugins), original_path])
    )
    checks: list[str] = []

    trailing_home = work / "trailing home"
    trailing_home.mkdir()
    _, trailing = invoke_probe(
        zed,
        [
            "literal-before",
            "--home",
            trailing_home,
            "--git-submodules=false",
            "literal-after",
        ],
        environment=environment,
        marker=work / "trailing.json",
    )
    require(
        trailing.get("args") == ["literal-before", "literal-after"],
        f"recognized trailing root options leaked into child argv: {trailing}",
    )
    require(
        trailing.get("env")
        == {
            "ZED_EXTERNAL_SUBCOMMAND": "probe",
            "ZED_PKG_HOME": str(trailing_home),
            "ZED_PKG_GIT_SUBMODULES": "false",
        },
        f"recognized trailing root options did not become child environment: {trailing}",
    )
    checks.append("recognized-trailing-root-options-are-lifted")

    literal_arguments = [
        "literal-before",
        "--",
        "--home",
        "child-owned-home",
        "--git-submodules=false",
    ]
    _, after_double_dash = invoke_probe(
        zed,
        literal_arguments,
        environment=environment,
        marker=work / "double-dash.json",
    )
    require(
        after_double_dash.get("args") == literal_arguments,
        f"literal double dash did not terminate root-option extraction: {after_double_dash}",
    )
    require(
        after_double_dash.get("env")
        == {
            "ZED_EXTERNAL_SUBCOMMAND": "probe",
            "ZED_PKG_HOME": "",
            "ZED_PKG_GIT_SUBMODULES": "",
        },
        f"arguments after double dash changed root-option environment: {after_double_dash}",
    )
    checks.append("literal-double-dash-terminates-root-option-extraction")

    malformed_marker = work / "malformed.json"
    malformed_environment = dict(environment)
    malformed_environment["ZED_PROBE_OUTPUT"] = str(malformed_marker)
    malformed_environment["ZED_PROBE_EXIT"] = "0"
    malformed = run(
        [zed, "probe", "--git-submodules=maybe", "payload"],
        environment=malformed_environment,
    )
    require(malformed.returncode != 0, "malformed trailing boolean was accepted")
    require(
        not malformed_marker.exists(),
        "malformed trailing boolean reached the external executable",
    )
    checks.append("malformed-trailing-root-boolean-fails-closed")

    return {
        "$schema": "zed-pkg-test/gitops-dispatch-trailing-options/v1",
        "candidate": args.candidate,
        "result": "passed",
        "checkCount": len(checks),
        "checks": checks,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", type=Path, required=True)
    parser.add_argument("--probe", type=Path, required=True)
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
        f"certified {evidence['checkCount']} trailing-option checks "
        f"against {evidence['candidate']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
