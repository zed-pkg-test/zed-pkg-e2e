#!/usr/bin/env python3
"""Black-box certification for checkout-local Git-submodule takeover locking."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Mapping, Sequence


class ContractError(RuntimeError):
    """Raised when an observable process-lock contract is violated."""


class Contract:
    def __init__(self, zed: Path, work_root: Path) -> None:
        if os.name == "nt":
            raise ContractError("this process-group canary requires a POSIX host")

        self.zed = zed.resolve()
        self.root = work_root.resolve()
        self.real_git = Path(shutil.which("git") or "").resolve()
        if not self.zed.is_file():
            raise ContractError(f"zed executable not found: {self.zed}")
        if not self.real_git.is_file():
            raise ContractError("git executable not found")
        if self.root.exists():
            raise ContractError(f"work root must not already exist: {self.root}")

        self.registry = self.root / "registry"
        self.homes = self.root / "homes"
        self.repos = self.root / "repos"
        self.runs = self.root / "runs"
        self.shim_dir = self.root / "git-shim"
        self.evidence = self.root / "evidence"
        for directory in (
            self.registry,
            self.homes,
            self.repos,
            self.runs,
            self.shim_dir,
            self.evidence,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        self.log_path = self.evidence / "contract.log"
        self.log_path.write_text("", encoding="utf-8")
        self.checks: list[str] = []
        self._write_git_shim()

    def _write_git_shim(self) -> None:
        shim = self.shim_dir / "git"
        shim.write_text(
            f"""#!{sys.executable}
import os
import pathlib
import sys
import time

args = sys.argv[1:]
if (
    os.environ.get("ZED_TEST_BLOCK_SUBMODULE_SYNC") == "1"
    and "submodule" in args
    and "sync" in args
):
    ready = pathlib.Path(os.environ["ZED_TEST_READY"])
    release = pathlib.Path(os.environ["ZED_TEST_RELEASE"])
    ready.write_text("ready\\n", encoding="utf-8")
    deadline = time.monotonic() + 120.0
    while not release.exists():
        if time.monotonic() >= deadline:
            print("timed out waiting for test release", file=sys.stderr)
            raise SystemExit(98)
        time.sleep(0.02)

os.execv({str(self.real_git)!r}, [{str(self.real_git)!r}, *args])
""",
            encoding="utf-8",
        )
        shim.chmod(0o755)

    def log(self, text: str) -> None:
        print(text, flush=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(text)
            if not text.endswith("\n"):
                handle.write("\n")

    def base_env(self, home: Path) -> dict[str, str]:
        env = os.environ.copy()
        for key in (
            "ZED_PKG_GIT_SUBMODULES",
            "ZED_PKG_TOKEN",
            "ZED_TOKEN",
            "GH_TOKEN",
            "GITHUB_TOKEN",
            "CLOUDFLARE_API_TOKEN",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
        ):
            env.pop(key, None)
        home.mkdir(parents=True, exist_ok=True)
        env.update(
            {
                "HOME": str(home),
                "USERPROFILE": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "ZED_PKG_HOME": str(home / ".zed-pkg"),
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": "true",
                "GIT_AUTHOR_NAME": "Zed Lock Contract",
                "GIT_AUTHOR_EMAIL": "zed-lock-contract@example.invalid",
                "GIT_COMMITTER_NAME": "Zed Lock Contract",
                "GIT_COMMITTER_EMAIL": "zed-lock-contract@example.invalid",
            }
        )
        return env

    def run(
        self,
        argv: Sequence[str | Path],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        expect: int = 0,
    ) -> subprocess.CompletedProcess[str]:
        command = [str(value) for value in argv]
        merged = self.base_env(self.homes / "setup")
        if env:
            merged.update(env)
        self.log(f"\n$ (cd {cwd or Path.cwd()} && {' '.join(command)})")
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=merged,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if completed.stdout:
            self.log(completed.stdout)
        if completed.returncode != expect:
            raise ContractError(
                f"command returned {completed.returncode}, expected {expect}: "
                f"{' '.join(command)}\n{completed.stdout}"
            )
        return completed

    def git(self, project: Path, *args: str) -> str:
        return self.run([self.real_git, "-C", project, *args], cwd=project).stdout

    def init_repo(self, project: Path) -> None:
        project.mkdir(parents=True)
        self.run([self.real_git, "init", "-b", "main", project], cwd=self.root)
        self.git(project, "config", "user.name", "Zed Lock Contract")
        self.git(project, "config", "user.email", "zed-lock-contract@example.invalid")

    def commit_all(self, project: Path, message: str) -> str:
        self.git(project, "add", "-A")
        self.git(project, "commit", "-m", message)
        return self.git(project, "rev-parse", "HEAD").strip()

    @staticmethod
    def write_package(project: Path, name: str) -> bytes:
        payload = f"""[package]
org = "acme"
name = "{name}"
version = "1.2.3"

[package.repository]
vcs = "git"
url = "https://example.invalid/acme/{name}.git"
""".encode()
        (project / ".zpkg.toml").write_bytes(payload)
        return payload

    def fixture(self, name: str) -> tuple[Path, Path]:
        child = self.repos / f"{name}-child"
        self.init_repo(child)
        self.write_package(child, f"{name}-child")
        (child / "payload.txt").write_text(
            f"payload for {name}\n", encoding="utf-8"
        )
        self.commit_all(child, "child package")

        root = self.repos / f"{name}-root"
        self.init_repo(root)
        self.write_package(root, f"{name}-root")
        self.git(root, "config", "protocol.file.allow", "always")
        self.run(
            [
                self.real_git,
                "-C",
                root,
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "add",
                child,
                "vendor/child",
            ],
            cwd=root,
        )
        self.commit_all(root, "root with child submodule")
        self.git(root, "submodule", "deinit", "--force", "--", "vendor/child")
        return child, root

    def zed_command(self, home_name: str, *args: str) -> list[str]:
        home = self.homes / home_name
        home.mkdir(parents=True, exist_ok=True)
        return [
            str(self.zed),
            "--registry",
            f"file://{self.registry}",
            "--home",
            str(home),
            *args,
        ]

    def start(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.Popen[str]:
        merged = self.base_env(self.homes / "process")
        if env:
            merged.update(env)
        self.log(f"\n$ background (cd {cwd} && {' '.join(command)})")
        return subprocess.Popen(
            list(command),
            cwd=cwd,
            env=merged,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def wait_ready(
        self, ready: Path, process: subprocess.Popen[str], timeout: float = 30.0
    ) -> None:
        deadline = time.monotonic() + timeout
        while not ready.exists():
            if process.poll() is not None:
                output = process.communicate()[0]
                raise ContractError(
                    f"holder exited before reaching blocked Git sync: {process.returncode}\n{output}"
                )
            if time.monotonic() >= deadline:
                raise ContractError("timed out waiting for holder to reach Git sync")
            time.sleep(0.02)

    @staticmethod
    def require_blocked(process: subprocess.Popen[str], duration: float = 0.8) -> None:
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            if process.poll() is not None:
                output = process.communicate()[0]
                raise ContractError(
                    f"contending mutation exited instead of blocking: "
                    f"{process.returncode}\n{output}"
                )
            time.sleep(0.02)

    def finish(
        self,
        process: subprocess.Popen[str],
        *,
        expected: int,
        timeout: float = 60.0,
    ) -> str:
        try:
            output, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            os.killpg(process.pid, signal.SIGKILL)
            output, _ = process.communicate()
            raise ContractError(f"process timed out\n{output}") from error
        if output:
            self.log(output)
        if process.returncode != expected:
            raise ContractError(
                f"process returned {process.returncode}, expected {expected}\n{output}"
            )
        return output

    def blocked_holder(
        self, root: Path, scenario: str
    ) -> tuple[subprocess.Popen[str], Path, Path]:
        ready = self.runs / f"{scenario}.ready"
        release = self.runs / f"{scenario}.release"
        path = f"{self.shim_dir}{os.pathsep}{os.environ.get('PATH', '')}"
        env = {
            "PATH": path,
            "ZED_TEST_BLOCK_SUBMODULE_SYNC": "1",
            "ZED_TEST_READY": str(ready),
            "ZED_TEST_RELEASE": str(release),
        }
        process = self.start(
            self.zed_command(
                f"{scenario}-holder", "overtake", "--git-submodules"
            ),
            cwd=root,
            env=env,
        )
        self.wait_ready(ready, process)
        lock_path = root / ".zed/operation.lock"
        if not lock_path.is_file():
            raise ContractError(
                "overtake reached Git transport before publishing its operation lock"
            )
        return process, ready, release

    def certify_normal_serialization(self) -> dict[str, float | int]:
        _, root = self.fixture("normal")
        alias = self.runs / "normal-root-alias"
        alias.symlink_to(root, target_is_directory=True)

        holder, _, release = self.blocked_holder(root, "normal")
        started = time.monotonic()
        waiter = self.start(
            self.zed_command("normal-waiter", "install", "--frozen"),
            cwd=alias,
        )
        self.require_blocked(waiter)
        blocked_for = time.monotonic() - started
        if (root / ".zpkg.lock").exists():
            raise ContractError("waiter observed a lockfile before takeover was released")

        release.write_text("release\n", encoding="utf-8")
        holder_output = self.finish(holder, expected=0)
        waiter_output = self.finish(waiter, expected=0)
        if "overtook 1 Git submodule package(s)" not in holder_output:
            raise ContractError("successful holder did not report one adopted package")
        if not (root / ".zpkg.lock").is_file():
            raise ContractError("takeover did not publish the lockfile")
        if not (root / "zed_modules/acme/normal-child/payload.txt").is_file():
            raise ContractError("serialized frozen install did not materialize the package")
        if "error:" in waiter_output.lower():
            raise ContractError("serialized waiter reported an error")

        self.checks.extend(
            [
                "overtake owns operation.lock before Git transport begins",
                "symlink-alias install blocks behind active takeover",
                "blocked frozen install succeeds after takeover publishes lock state",
            ]
        )
        return {
            "holder_pid": holder.pid,
            "waiter_pid": waiter.pid,
            "observed_block_seconds": round(blocked_for, 3),
        }

    def certify_owner_termination_release(self) -> dict[str, float | int]:
        _, root = self.fixture("terminated")
        original_manifest = (root / ".zpkg.toml").read_bytes()
        holder, _, _ = self.blocked_holder(root, "terminated")

        waiter = self.start(
            self.zed_command("terminated-waiter", "install"),
            cwd=root,
        )
        started = time.monotonic()
        self.require_blocked(waiter)
        blocked_for = time.monotonic() - started

        os.killpg(holder.pid, signal.SIGTERM)
        holder_output = self.finish(holder, expected=-signal.SIGTERM)
        waiter_output = self.finish(waiter, expected=0)
        if holder_output:
            self.log(holder_output)
        if "error:" in waiter_output.lower():
            raise ContractError("waiter reported an error after owner termination")
        if (root / ".zpkg.toml").read_bytes() != original_manifest:
            raise ContractError("terminated takeover changed root manifest bytes")
        if (root / ".zpkg-staging").exists():
            raise ContractError("terminated pre-mutation takeover left recovery state")
        manifest = tomllib.loads((root / ".zpkg.toml").read_text(encoding="utf-8"))
        if manifest.get("dependencies"):
            raise ContractError("terminated takeover left adopted dependency intent")
        if (root / "vendor/child/.zpkg.toml").exists():
            raise ContractError("holder passed the intentionally blocked Git sync")

        self.checks.extend(
            [
                "independent install blocks behind takeover owner",
                "kernel lock releases when takeover owner process terminates",
                "terminated pre-mutation takeover leaves authored manifest and recovery state intact",
            ]
        )
        return {
            "holder_pid": holder.pid,
            "waiter_pid": waiter.pid,
            "observed_block_seconds": round(blocked_for, 3),
        }

    def finish_evidence(
        self,
        normal: Mapping[str, float | int],
        terminated: Mapping[str, float | int],
    ) -> None:
        if len(self.checks) != 6:
            raise ContractError(f"unexpected check count: {self.checks}")
        binary_sha = hashlib.sha256(self.zed.read_bytes()).hexdigest()
        version = self.run([self.zed, "--version"]).stdout.strip()
        evidence = {
            "schema": "zed.git-submodule-overtake-operation-lock/v1",
            "zed_version": version,
            "zed_sha256": binary_sha,
            "checks": self.checks,
            "normal_completion": dict(normal),
            "owner_termination": dict(terminated),
            "network_credentials": False,
            "public_registry_mutation": False,
        }
        (self.evidence / "evidence.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.log(f"\ncertified {len(self.checks)} takeover operation-lock checks")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    contract = Contract(args.zed, args.work_root)
    normal = contract.certify_normal_serialization()
    terminated = contract.certify_owner_termination_release()
    contract.finish_evidence(normal, terminated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
