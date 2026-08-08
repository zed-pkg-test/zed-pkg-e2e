#!/usr/bin/env python3
"""Independent black-box acceptance for Zed terminal-context prompt behavior.

This script intentionally lives in zed-pkg-test rather than zed-cli. It consumes
an exact built candidate and exact test-org fixtures, then proves that prompt
safety remains fail-closed unless the documented context contract is satisfied.
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
        print(completed.stderr, end="" if completed.stderr.endswith("\n") else "\n", file=sys.stderr)

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


def assert_installed(app: Path) -> None:
    package = app / ".vendor" / ".zed" / "zed-pkg-test" / "rust-lib"
    if not package.is_dir():
        raise SystemExit(f"expected installed package directory: {package}")


def assert_uninstalled(app: Path) -> None:
    package = app / ".vendor" / ".zed" / "zed-pkg-test" / "rust-lib"
    if package.exists():
        raise SystemExit(f"package still materialized after uninstall: {package}")


def install(zed: Path, app: Path, registry_uri: str, home: Path) -> None:
    run(
        [
            str(zed),
            "install",
            "--registry",
            registry_uri,
            "--home",
            str(home),
            "--install-mode",
            "copy",
        ],
        cwd=app,
        env=clean_env(),
    )
    assert_installed(app)


def interactive_uninstall(
    zed: Path,
    app: Path,
    home: Path,
    *,
    env: dict[str, str],
    should_succeed: bool,
) -> subprocess.CompletedProcess[str]:
    return run(
        [str(zed), "--interactive", "--home", str(home), "uninstall"],
        cwd=app,
        env=env,
        input_text="yes\n",
        should_succeed=should_succeed,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed-cli-root", required=True)
    parser.add_argument("--fixture-lib", required=True)
    parser.add_argument("--fixture-app", required=True)
    args = parser.parse_args()

    zed_cli_root = Path(args.zed_cli_root).resolve()
    fixture_lib = Path(args.fixture_lib).resolve()
    fixture_app = Path(args.fixture_app).resolve()
    zed = zed_cli_root / "target" / "debug" / ("zed.exe" if os.name == "nt" else "zed")
    if not zed.is_file():
        raise SystemExit(f"built zed candidate not found: {zed}")

    runner_temp = Path(os.environ.get("RUNNER_TEMP", ".")).resolve()
    work = runner_temp / "zed-terminal-context-acceptance"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    lib = work / "rust-lib"
    app = work / "rust-app"
    registry = work / "registry"
    home = work / "home"
    shutil.copytree(fixture_lib, lib, ignore=shutil.ignore_patterns(".git"))
    shutil.copytree(fixture_app, app, ignore=shutil.ignore_patterns(".git"))
    registry.mkdir()
    home.mkdir()
    registry_uri = registry.as_uri()

    # Publish the exact test-org fixture into a local file registry. The fixture
    # has no build hook, so this is portable across Linux, macOS, and Windows.
    run(
        [str(zed), "publish", "--registry", registry_uri, "--skip-vcs-checks"],
        cwd=lib,
        env=clean_env(),
    )
    install(zed, app, registry_uri, home)

    # A pipe must never masquerade as human consent. GitHub Actions is also CI,
    # so this independently confirms the ordinary fail-closed path.
    ordinary = interactive_uninstall(
        zed,
        app,
        home,
        env=clean_env(),
        should_succeed=False,
    )
    assert_contains_prompt_failure(ordinary)
    assert_installed(app)

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
    ci_failure = interactive_uninstall(
        zed,
        app,
        home,
        env=forced_ci,
        should_succeed=False,
    )
    assert_contains_prompt_failure(ci_failure)
    assert_installed(app)

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
    dumb_failure = interactive_uninstall(
        zed,
        app,
        home,
        env=dumb,
        should_succeed=False,
    )
    assert_contains_prompt_failure(dumb_failure)
    assert_installed(app)

    # The documented ZED_PKG_FORCE_* test controls may explicitly simulate a
    # safe human terminal. stdout remains redirected/captured here, proving it
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
    accepted = interactive_uninstall(
        zed,
        app,
        home,
        env=zed_override,
        should_succeed=True,
    )
    if "[y/N]" not in accepted.stderr:
        raise SystemExit("accepted interactive mutation did not emit its checkpoint on stderr")
    assert_uninstalled(app)

    # Reinstall, then prove the shared F2E_FORCE_* spellings drive the same
    # behavior without any Zed-specific force variables present.
    install(zed, app, registry_uri, home)
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
    accepted_f2e = interactive_uninstall(
        zed,
        app,
        home,
        env=f2e_override,
        should_succeed=True,
    )
    if "[y/N]" not in accepted_f2e.stderr:
        raise SystemExit("F2E override path did not emit its checkpoint on stderr")
    assert_uninstalled(app)

    # Unix runners additionally exercise a real PTY through the production
    # harness. Windows uses the deterministic override path above because the
    # upstream helper relies on forkpty().
    if os.name != "nt":
        install(zed, app, registry_uri, home)
        helper = zed_cli_root / "tests" / "interactive_pty.py"
        run(
            [
                sys.executable,
                str(helper),
                "yes",
                "--",
                str(zed),
                "--interactive",
                "--home",
                str(home),
                "uninstall",
            ],
            cwd=app,
            env=clean_env(),
        )
        assert_uninstalled(app)

    print("terminal-context acceptance passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
