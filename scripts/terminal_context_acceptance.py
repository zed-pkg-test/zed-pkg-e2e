#!/usr/bin/env python3
"""Independent black-box acceptance for Zed terminal-context prompt behavior.

This script intentionally lives in zed-pkg-test rather than zed-cli. It consumes
an exact built candidate and drives a minimal real mutation (`zed init`) so prompt
safety is certified without depending on registry, network, or package transport.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

FORCE_KEYS = {
    "ZED_PKG_FORCE_STDIN_TTY",
    "ZED_PKG_FORCE_STDOUT_TTY",
    "ZED_PKG_FORCE_STDERR_TTY",
    "ZED_PKG_FORCE_CI",
    "ZED_PKG_FORCE_COLOR",
    "ZED_PKG_FORCE_UNICODE",
    "ZED_PKG_SHELL",
    "F2E_FORCE_STDIN_TTY",
    "F2E_FORCE_STDOUT_TTY",
    "F2E_FORCE_STDERR_TTY",
    "F2E_FORCE_CI",
    "F2E_FORCE_COLOR",
    "F2E_FORCE_UNICODE",
    "F2E_SHELL",
}


def clean_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in FORCE_KEYS:
        env.pop(key, None)
    return env


def run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    should_succeed: bool = True,
) -> subprocess.CompletedProcess[str]:
    print(f"$ {' '.join(command)}", flush=True)
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if completed.stderr:
        print(
            completed.stderr,
            end="" if completed.stderr.endswith("\n") else "\n",
            file=sys.stderr,
        )

    succeeded = completed.returncode == 0
    if succeeded != should_succeed:
        expected = "success" if should_succeed else "failure"
        raise SystemExit(
            f"expected {expected}, got exit {completed.returncode}: {' '.join(command)}"
        )
    return completed


def assert_contains_prompt_failure(completed: subprocess.CompletedProcess[str]) -> None:
    combined = f"{completed.stdout}\n{completed.stderr}".lower()
    if "terminal stdin and stderr" not in combined:
        raise SystemExit(
            "interactive failure did not explain that terminal stdin and stderr are required"
        )


def reset_project(project: Path) -> None:
    for name in (".zpkg.toml", ".gitignore"):
        path = project / name
        if path.exists():
            path.unlink()


def assert_not_initialized(project: Path) -> None:
    if (project / ".zpkg.toml").exists():
        raise SystemExit("rejected confirmation unexpectedly created .zpkg.toml")


def assert_initialized(project: Path) -> None:
    manifest = project / ".zpkg.toml"
    if not manifest.is_file():
        raise SystemExit("accepted confirmation did not create .zpkg.toml")
    text = manifest.read_text(encoding="utf-8")
    if 'org = "terminal-cert"' not in text or 'name = "prompt-fixture"' not in text:
        raise SystemExit("generated manifest did not contain the requested package identity")


def interactive_init(
    zed: Path,
    project: Path,
    *,
    env: dict[str, str],
    should_succeed: bool,
) -> subprocess.CompletedProcess[str]:
    return run(
        [
            str(zed),
            "--interactive",
            "init",
            "--org",
            "terminal-cert",
            "--name",
            "prompt-fixture",
        ],
        cwd=project,
        env=env,
        input_text="yes\n",
        should_succeed=should_succeed,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed-cli-root", required=True)
    args = parser.parse_args()

    zed_cli_root = Path(args.zed_cli_root).resolve()
    zed = zed_cli_root / "target" / "debug" / ("zed.exe" if os.name == "nt" else "zed")
    if not zed.is_file():
        raise SystemExit(f"built zed candidate not found: {zed}")

    runner_temp = Path(os.environ.get("RUNNER_TEMP", ".")).resolve()
    work = runner_temp / "zed-terminal-context-acceptance"
    if work.exists():
        shutil.rmtree(work)
    project = work / "prompt-fixture"
    project.mkdir(parents=True)

    # A pipe must never masquerade as human consent. GitHub Actions is also CI,
    # so this independently confirms the ordinary fail-closed path.
    ordinary = interactive_init(
        zed,
        project,
        env=clean_env(),
        should_succeed=False,
    )
    assert_contains_prompt_failure(ordinary)
    assert_not_initialized(project)

    # Even when terminal state is forced true, CI remains authoritative.
    forced_ci = clean_env()
    forced_ci.update(
        {
            "ZED_PKG_FORCE_STDIN_TTY": "1",
            "ZED_PKG_FORCE_STDOUT_TTY": "0",
            "ZED_PKG_FORCE_STDERR_TTY": "1",
            "ZED_PKG_FORCE_CI": "1",
            "TERM": "xterm-256color",
        }
    )
    ci_failure = interactive_init(
        zed,
        project,
        env=forced_ci,
        should_succeed=False,
    )
    assert_contains_prompt_failure(ci_failure)
    assert_not_initialized(project)

    # TERM=dumb also remains fail-closed even after CI is explicitly cleared.
    dumb = clean_env()
    dumb.update(
        {
            "ZED_PKG_FORCE_STDIN_TTY": "1",
            "ZED_PKG_FORCE_STDOUT_TTY": "0",
            "ZED_PKG_FORCE_STDERR_TTY": "1",
            "ZED_PKG_FORCE_CI": "0",
            "TERM": "dumb",
        }
    )
    dumb_failure = interactive_init(
        zed,
        project,
        env=dumb,
        should_succeed=False,
    )
    assert_contains_prompt_failure(dumb_failure)
    assert_not_initialized(project)

    # The documented ZED_PKG_FORCE_* controls may explicitly simulate a safe
    # human terminal. stdout remains redirected/captured here, proving stdout
    # is not part of the prompt gate.
    zed_override = clean_env()
    zed_override.update(
        {
            "ZED_PKG_FORCE_STDIN_TTY": "1",
            "ZED_PKG_FORCE_STDOUT_TTY": "0",
            "ZED_PKG_FORCE_STDERR_TTY": "1",
            "ZED_PKG_FORCE_CI": "0",
            "TERM": "xterm-256color",
        }
    )
    accepted = interactive_init(
        zed,
        project,
        env=zed_override,
        should_succeed=True,
    )
    if "[y/N]" not in accepted.stderr:
        raise SystemExit("accepted interactive mutation did not emit its checkpoint on stderr")
    assert_initialized(project)

    # Reset only test-created files, then prove the shared F2E_FORCE_* spellings
    # drive the same behavior without any Zed-specific force variables present.
    reset_project(project)
    f2e_override = clean_env()
    f2e_override.update(
        {
            "F2E_FORCE_STDIN_TTY": "1",
            "F2E_FORCE_STDOUT_TTY": "0",
            "F2E_FORCE_STDERR_TTY": "1",
            "F2E_FORCE_CI": "0",
            "TERM": "xterm-256color",
        }
    )
    accepted_f2e = interactive_init(
        zed,
        project,
        env=f2e_override,
        should_succeed=True,
    )
    if "[y/N]" not in accepted_f2e.stderr:
        raise SystemExit("F2E override path did not emit its checkpoint on stderr")
    assert_initialized(project)

    # Unix runners additionally exercise a real PTY through the production
    # harness. Windows uses the deterministic override path above because the
    # upstream helper relies on forkpty().
    if os.name != "nt":
        reset_project(project)
        helper = zed_cli_root / "tests" / "interactive_pty.py"
        run(
            [
                sys.executable,
                str(helper),
                "yes",
                "--",
                str(zed),
                "--interactive",
                "init",
                "--org",
                "terminal-cert",
                "--name",
                "prompt-fixture",
            ],
            cwd=project,
            env=clean_env(),
        )
        assert_initialized(project)

    print("terminal-context acceptance passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
