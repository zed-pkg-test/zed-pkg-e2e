#!/usr/bin/env python3
"""Black-box acceptance for the shared Zed/Git-submodule compatibility switch."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tomllib
from pathlib import Path
from typing import Mapping, Sequence

MANIFEST = ".zpkg.toml"
LOCKFILE = ".zpkg.lock"
SUBMODULE_PATH = Path("vendor/client")
NESTED_PATH = Path("vendor/client/vendor/leaf/leaf.txt")


class Contract:
    def __init__(self, zed: Path, work_root: Path) -> None:
        self.zed = zed.resolve()
        self.root = work_root.resolve()
        self.registry = self.root / "registry"
        self.homes = self.root / "homes"
        self.repos = self.root / "repos"
        self.runs = self.root / "runs"
        self.evidence = self.root / "evidence"
        self.log_path = self.evidence / "contract.log"
        self.checks: list[str] = []

        if not self.zed.is_file():
            raise RuntimeError(f"zed binary not found: {self.zed}")
        if self.root.exists():
            raise RuntimeError(f"work root must not already exist: {self.root}")
        for directory in (
            self.registry,
            self.homes,
            self.repos,
            self.runs,
            self.evidence,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("", encoding="utf-8")

    def environment(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        for key in (
            "ZED_PKG_GIT_SUBMODULES",
            "ZED_PKG_TOKEN",
            "ZED_TOKEN",
            "GH_TOKEN",
            "GITHUB_TOKEN",
        ):
            env.pop(key, None)
        env.update(
            {
                "CI": "true",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": "true",
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "protocol.file.allow",
                "GIT_CONFIG_VALUE_0": "always",
                "GIT_AUTHOR_NAME": "Zed Contract",
                "GIT_AUTHOR_EMAIL": "zed-contract@example.invalid",
                "GIT_COMMITTER_NAME": "Zed Contract",
                "GIT_COMMITTER_EMAIL": "zed-contract@example.invalid",
            }
        )
        if extra:
            env.update(extra)
        return env

    def log(self, text: str) -> None:
        print(text, flush=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(text)
            if not text.endswith("\n"):
                handle.write("\n")

    def run(
        self,
        argv: Sequence[str | Path],
        *,
        cwd: Path | None = None,
        extra_env: Mapping[str, str] | None = None,
        should_fail: bool = False,
    ) -> str:
        command = [str(value) for value in argv]
        rendered = " ".join(json.dumps(value) for value in command)
        self.log(f"\n$ (cd {cwd or Path.cwd()} && {rendered})")
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=self.environment(extra_env),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if completed.stdout:
            self.log(completed.stdout)
        if should_fail:
            if completed.returncode == 0:
                raise AssertionError(f"command unexpectedly succeeded: {rendered}")
        elif completed.returncode != 0:
            raise RuntimeError(
                f"command failed with exit code {completed.returncode}: {rendered}\n"
                f"{completed.stdout}"
            )
        return completed.stdout

    def git(self, project: Path, *args: str) -> str:
        return self.run(["git", "-C", project, *args], cwd=project)

    def zed_cmd(
        self,
        project: Path,
        home_name: str,
        *args: str,
        extra_env: Mapping[str, str] | None = None,
        should_fail: bool = False,
    ) -> str:
        home = self.homes / home_name
        home.mkdir(parents=True, exist_ok=True)
        return self.run(
            [
                self.zed,
                "--registry",
                f"file://{self.registry}",
                "--home",
                home,
                *args,
            ],
            cwd=project,
            extra_env=extra_env,
            should_fail=should_fail,
        )

    def init_repo(self, path: Path) -> None:
        path.mkdir(parents=True)
        self.run(["git", "init", "-b", "main", path], cwd=self.root)
        self.git(path, "config", "user.name", "Zed Contract")
        self.git(path, "config", "user.email", "zed-contract@example.invalid")

    def commit_all(self, path: Path, message: str) -> str:
        self.git(path, "add", "-A")
        self.git(path, "commit", "-m", message)
        return self.git(path, "rev-parse", "HEAD").strip()

    @staticmethod
    def write_package(path: Path, org: str, name: str) -> None:
        (path / MANIFEST).write_text(
            f'''[package]
org = "{org}"
name = "{name}"
version = "1.2.3"

[package.repository]
vcs = "git"
url = "https://example.invalid/{org}/{name}.git"
''',
            encoding="utf-8",
        )

    def add_submodule(self, project: Path, source: Path, destination: str) -> None:
        self.run(
            [
                "git",
                "-C",
                project,
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "add",
                source,
                destination,
            ],
            cwd=project,
        )

    def clone_without_submodules(self, source: Path, destination: Path) -> None:
        self.run(
            ["git", "clone", "--no-recurse-submodules", source, destination],
            cwd=self.root,
        )
        self.git(destination, "config", "user.name", "Zed Contract")
        self.git(destination, "config", "user.email", "zed-contract@example.invalid")

    def build_fixture(self) -> tuple[Path, str]:
        leaf = self.repos / "leaf"
        self.init_repo(leaf)
        (leaf / "leaf.txt").write_text("nested leaf\n", encoding="utf-8")
        self.commit_all(leaf, "leaf")

        child = self.repos / "client"
        self.init_repo(child)
        self.write_package(child, "acme", "client")
        (child / "payload.txt").write_text("client payload\n", encoding="utf-8")
        self.add_submodule(child, leaf, "vendor/leaf")
        child_commit = self.commit_all(child, "client with nested leaf")

        root = self.repos / "root"
        self.init_repo(root)
        self.write_package(root, "acme", "root")
        self.add_submodule(root, child, SUBMODULE_PATH.as_posix())
        self.commit_all(root, "root with package submodule")
        return root, child_commit

    @staticmethod
    def assert_uninitialized(project: Path) -> None:
        assert not (project / SUBMODULE_PATH / "payload.txt").exists()
        assert not (project / NESTED_PATH).exists()

    @staticmethod
    def assert_initialized(project: Path) -> None:
        assert (project / SUBMODULE_PATH / "payload.txt").is_file()
        assert (project / NESTED_PATH).is_file()

    def certify_install_switch(self, root_source: Path) -> None:
        default_off = self.runs / "install-default-off"
        self.clone_without_submodules(root_source, default_off)
        self.zed_cmd(default_off, "install-default-off", "install")
        self.assert_uninitialized(default_off)
        self.checks.append("install leaves Git transport disabled by default")

        prefix_on = self.runs / "install-prefix-on"
        self.clone_without_submodules(root_source, prefix_on)
        self.zed_cmd(
            prefix_on,
            "install-prefix-on",
            "--git-submodules",
            "install",
            extra_env={"ZED_PKG_GIT_SUBMODULES": "definitely-invalid"},
        )
        self.assert_initialized(prefix_on)
        self.checks.append(
            "global prefix flag enables recursive install and overrides invalid environment"
        )

        suffix_off = self.runs / "install-suffix-off"
        self.clone_without_submodules(root_source, suffix_off)
        self.zed_cmd(
            suffix_off,
            "install-suffix-off",
            "install",
            "--git-submodules=false",
            extra_env={"ZED_PKG_GIT_SUBMODULES": "ON"},
        )
        self.assert_uninitialized(suffix_off)
        self.checks.append("explicit false overrides inherited install mode")

        environment_on = self.runs / "install-environment-on"
        self.clone_without_submodules(root_source, environment_on)
        self.zed_cmd(
            environment_on,
            "install-environment-on",
            "install",
            extra_env={"ZED_PKG_GIT_SUBMODULES": "yes"},
        )
        self.assert_initialized(environment_on)
        self.checks.append("environment-only install mode initializes recursively")

    def certify_overtake_switch(self, root_source: Path, child_commit: str) -> None:
        project = self.runs / "overtake"
        self.clone_without_submodules(root_source, project)

        output = self.zed_cmd(
            project,
            "overtake",
            "--git-submodules",
            "overtake",
        )
        assert "overtook 1 Git submodule package(s)" in output
        self.assert_initialized(project)

        manifest_before = (project / MANIFEST).read_bytes()
        lock_before = (project / LOCKFILE).read_bytes()
        manifest = tomllib.loads(manifest_before.decode("utf-8"))
        assert manifest["dependencies"]["acme/client"] == "=1.2.3"
        assert SUBMODULE_PATH.as_posix() in manifest["workspace"]["members"]

        lock = tomllib.loads(lock_before.decode("utf-8"))
        entries = lock.get("git-submodule")
        assert isinstance(entries, list) and len(entries) == 1
        entry = entries[0]
        assert entry["path"] == SUBMODULE_PATH.as_posix()
        assert entry["package"] == "acme/client"
        assert entry["version"] == "1.2.3"
        assert entry["commit"] == child_commit
        assert len(entry["sha256"]) == 64
        int(entry["sha256"], 16)
        assert entry["size"] > 0
        self.checks.append("global prefix takeover adopts .gitmodules into manifest and lock")

        self.zed_cmd(
            project,
            "overtake",
            "overtake",
            "--git-submodules",
        )
        assert (project / MANIFEST).read_bytes() == manifest_before
        assert (project / LOCKFILE).read_bytes() == lock_before
        self.checks.append("command suffix takeover is idempotent")

        self.zed_cmd(
            project,
            "overtake",
            "overtake",
            extra_env={"ZED_PKG_GIT_SUBMODULES": "on"},
        )
        assert (project / MANIFEST).read_bytes() == manifest_before
        assert (project / LOCKFILE).read_bytes() == lock_before
        self.checks.append("environment-only takeover is idempotent")

        failure = self.zed_cmd(
            project,
            "overtake",
            "overtake",
            "--git-submodules=false",
            extra_env={"ZED_PKG_GIT_SUBMODULES": "true"},
            should_fail=True,
        )
        assert "no takeover source selected" in failure
        assert (project / MANIFEST).read_bytes() == manifest_before
        assert (project / LOCKFILE).read_bytes() == lock_before
        self.checks.append("explicit false disables takeover without mutating Zed state")

        (self.evidence / "adopted-manifest.toml").write_bytes(manifest_before)
        (self.evidence / "adopted-lock.toml").write_bytes(lock_before)

    def certify_help_surface(self) -> None:
        root_help = self.run([self.zed, "--help"])
        assert "--git-submodules" in root_help
        assert "overtake" in root_help

        takeover_help = self.run([self.zed, "overtake", "--help"])
        assert "--git-submodules" in takeover_help
        assert "ZED_PKG_GIT_SUBMODULES" in takeover_help
        self.checks.append("root and takeover help expose the shared compatibility switch")

    def write_evidence(self) -> None:
        evidence = {
            "schema": "zed.git-submodule-global-mode-evidence/v1",
            "checks": self.checks,
            "network_credentials": False,
            "public_registry_mutation": False,
        }
        (self.evidence / "evidence.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.log(f"\ncompleted {len(self.checks)} checks")

    def certify(self) -> None:
        root_source, child_commit = self.build_fixture()
        self.certify_install_switch(root_source)
        self.certify_overtake_switch(root_source, child_commit)
        self.certify_help_surface()
        self.write_evidence()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    Contract(args.zed, args.work_root).certify()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
