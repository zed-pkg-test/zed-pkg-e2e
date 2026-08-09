#!/usr/bin/env python3
"""Hermetic acceptance test for native dependencies and install hooks.

The test publishes two local Zed packages into a file registry, installs their
transitive graph with a fake apt-get binary, and verifies permission ordering,
argv safety, graph-wide de-duplication, writable staging, lifecycle ordering,
cache reuse, immutable source-store behavior, and Nix derivation purity.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Mapping, Sequence

ORG = "zed-pkg-test"


def shell_run(
    command: Sequence[str | Path],
    *,
    cwd: Path,
    env: Mapping[str, str],
    should_fail: bool = False,
) -> str:
    argv = [str(value) for value in command]
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env=dict(env),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    print(f"\n$ (cd {cwd} && {' '.join(argv)})", flush=True)
    print(completed.stdout, end="", flush=True)
    if should_fail:
        if completed.returncode == 0:
            raise AssertionError(f"command unexpectedly succeeded: {argv}")
    elif completed.returncode != 0:
        raise RuntimeError(
            f"command failed with exit code {completed.returncode}: {argv}"
        )
    return completed.stdout


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_executable(path: Path, content: str) -> None:
    write(path, content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def package_manifest(name: str, extra: str = "") -> str:
    return f'''[package]
org = "{ORG}"
name = "{name}"
version = "1.0.0"
description = "Native lifecycle acceptance fixture"
license = "MIT"

[package.repository]
url = "https://github.com/{ORG}/zed-pkg-e2e"

{extra.strip()}
'''


def consumer_manifest(name: str = "native-lifecycle-consumer") -> str:
    return f'''[package]
org = "{ORG}"
name = "{name}"
version = "0.0.0"

[package.repository]
url = "https://github.com/{ORG}/zed-pkg-e2e"

[dependencies]
"{ORG}/native-a" = "^1"
'''


def assert_absent(path: Path, message: str) -> None:
    if path.exists() or path.is_symlink():
        raise AssertionError(f"{message}: {path}")


def assert_contains(text: str, expected: str) -> None:
    if expected not in text:
        raise AssertionError(f"expected {expected!r} in output:\n{text}")


def copy_consumer(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / ".zpkg.toml", destination / ".zpkg.toml")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    args = parser.parse_args()

    zed = args.zed.resolve()
    root = args.work_root.resolve()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    registry = root / "registry"
    zed_home = root / "zed-home"
    registry.mkdir()
    zed_home.mkdir()

    base_env = os.environ.copy()
    base_env.update(
        {
            "CI": "true",
            "GIT_TERMINAL_PROMPT": "0",
            "ZED_PKG_REGISTRY": f"file://{registry}",
            "ZED_PKG_HOME": str(zed_home),
            "ZED_PKG_TOKEN": "",
        }
    )

    package_b = root / "packages" / "native-b"
    write(package_b / "src" / "lib.txt", "native b\n")
    write(
        package_b / ".zpkg.toml",
        package_manifest(
            "native-b",
            '''[native-dependencies]
apt = ["libssl-dev", "zlib1g-dev"]
nix = ["openssl", "zlib"]
''',
        ),
    )

    package_a = root / "packages" / "native-a"
    write(package_a / "src" / "lib.txt", "native a\n")
    write(
        package_a / ".zpkg.toml",
        package_manifest(
            "native-a",
            f'''[dependencies]
"{ORG}/native-b" = "^1"

[native-dependencies]
apt = ["pkg-config", "libssl-dev"]
nix = ["pkg-config", "openssl"]

[hooks]
pre-install = ['printf "pre\\n" >> "$HOOK_CAPTURE"; printf "pre\\n" > lifecycle.txt; printf "%s\\n" "$ZED_INSTALL_PHASE" > pre-phase.txt; printf "%s\\n" "$ZED_NATIVE_MANAGER" > native-manager.txt; printf "%s\\n" "$ZED_NATIVE_PACKAGES" > native-packages.json']
post-install = ['printf "post\\n" >> "$HOOK_CAPTURE"; printf "post\\n" >> lifecycle.txt']

[build]
command = 'printf "build\\n" >> "$HOOK_CAPTURE"; printf "build\\n" >> lifecycle.txt'
''',
        ),
    )

    for package in (package_b, package_a):
        shell_run(
            [zed, "publish", "--skip-vcs-checks"],
            cwd=package,
            env=base_env,
        )

    consumer = root / "consumer"
    consumer.mkdir()
    write(consumer / ".zpkg.toml", consumer_manifest())

    fake_bin = root / "fake-bin"
    native_capture = root / "native.capture"
    hook_capture = root / "hooks.capture"
    write_executable(
        fake_bin / "apt-get",
        '''#!/bin/sh
set -eu
{
  printf 'call\\n'
  for arg do
    printf 'arg=%s\\n' "$arg"
  done
} >> "$NATIVE_CAPTURE"
''',
    )
    install_env = base_env.copy()
    install_env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{base_env.get('PATH', '')}",
            "NATIVE_CAPTURE": str(native_capture),
            "HOOK_CAPTURE": str(hook_capture),
        }
    )

    common = [
        zed,
        "install",
        "--install-mode",
        "copy",
        "--adapter",
        "none",
        "--native-manager",
        "apt",
    ]

    denied = shell_run(common, cwd=consumer, env=install_env, should_fail=True)
    assert_contains(denied, "--allow-native-deps")
    assert_absent(native_capture, "native manager ran before native consent")
    assert_absent(hook_capture, "hook ran before native consent")
    assert_absent(consumer / "zed_modules", "project materialized after denial")

    hooks_denied = shell_run(
        [*common, "--allow-native-deps"],
        cwd=consumer,
        env=install_env,
        should_fail=True,
    )
    assert_contains(hooks_denied, "--allow-install-hooks")
    assert_absent(native_capture, "native manager ran before hook consent")

    build_denied = shell_run(
        [*common, "--allow-native-deps", "--allow-install-hooks"],
        cwd=consumer,
        env=install_env,
        should_fail=True,
    )
    assert_contains(build_denied, "--allow-build")
    assert_absent(native_capture, "native manager ran before build consent")

    allowed = [
        *common,
        "--allow-native-deps",
        "--allow-install-hooks",
        "--allow-build",
    ]
    shell_run(allowed, cwd=consumer, env=install_env)

    native_lines = native_capture.read_text(encoding="utf-8").splitlines()
    if native_lines.count("call") != 1:
        raise AssertionError(f"expected one apt invocation, got: {native_lines}")
    if "arg=--" not in native_lines:
        raise AssertionError(f"apt option terminator missing: {native_lines}")
    for package in ("pkg-config", "libssl-dev", "zlib1g-dev"):
        if native_lines.count(f"arg={package}") != 1:
            raise AssertionError(
                f"native package {package!r} was not de-duplicated: {native_lines}"
            )

    installed = consumer / "zed_modules" / ORG / "native-a"
    if (installed / "lifecycle.txt").read_text(encoding="utf-8") != "pre\nbuild\npost\n":
        raise AssertionError("lifecycle hooks/build ran out of order")
    if (installed / "pre-phase.txt").read_text(encoding="utf-8") != "pre-install\n":
        raise AssertionError("pre-install phase metadata is incorrect")
    if (installed / "native-manager.txt").read_text(encoding="utf-8") != "apt\n":
        raise AssertionError("hook did not receive selected native manager")
    packages = json.loads(
        (installed / "native-packages.json").read_text(encoding="utf-8")
    )
    if packages != ["pkg-config", "libssl-dev"]:
        raise AssertionError(f"hook received wrong per-package native set: {packages}")
    if hook_capture.read_text(encoding="utf-8") != "pre\nbuild\npost\n":
        raise AssertionError("external lifecycle capture is incorrect")

    source_lifecycle = list((zed_home / "store").rglob("lifecycle.txt"))
    if source_lifecycle:
        raise AssertionError(f"immutable source store was mutated: {source_lifecycle}")
    cached_lifecycle = list((zed_home / "builds").rglob("lifecycle.txt"))
    if not cached_lifecycle:
        raise AssertionError("finalized lifecycle artifact was not promoted to cache")

    shell_run(allowed, cwd=consumer, env=install_env)
    if hook_capture.read_text(encoding="utf-8") != "pre\nbuild\npost\n":
        raise AssertionError("cache hit re-ran package-controlled lifecycle code")
    native_lines = native_capture.read_text(encoding="utf-8").splitlines()
    if native_lines.count("call") != 2:
        raise AssertionError("native prerequisites were not checked once per transaction")

    # A derivation may acknowledge inputs supplied by Nix, but Zed must never
    # execute a host package manager in the derivation sandbox.
    nix_consumer = root / "nix-consumer"
    copy_consumer(consumer, nix_consumer)
    nix_hook_capture = root / "nix-hooks.capture"
    nix_env = base_env.copy()
    nix_env.update(
        {
            "NIX_BUILD_TOP": str(root / "nix-build-top"),
            "HOOK_CAPTURE": str(nix_hook_capture),
            "NATIVE_CAPTURE": str(root / "must-not-exist.capture"),
        }
    )
    nix_common = [
        zed,
        "install",
        "--install-mode",
        "copy",
        "--adapter",
        "none",
        "--allow-native-deps",
        "--allow-install-hooks",
        "--allow-build",
        "--native-manager",
        "nix",
    ]
    nix_denied = shell_run(
        nix_common,
        cwd=nix_consumer,
        env=nix_env,
        should_fail=True,
    )
    assert_contains(nix_denied, "nativeBuildInputs/buildInputs")
    assert_absent(root / "must-not-exist.capture", "manager ran in Nix build")

    nix_env["ZED_PKG_NATIVE_DEPS_PROVIDED"] = "1"
    shell_run(nix_common, cwd=nix_consumer, env=nix_env)
    assert_absent(root / "must-not-exist.capture", "manager ran in acknowledged Nix build")
    if nix_hook_capture.read_text(encoding="utf-8") != "pre\nbuild\npost\n":
        raise AssertionError("acknowledged Nix lifecycle did not complete")

    print("native dependency and install-hook acceptance contract passed", flush=True)


if __name__ == "__main__":
    main()
