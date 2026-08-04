#!/usr/bin/env python3
"""Black-box certification for indirect .gitmodules metadata rejection."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Sequence

MANIFEST = ".zpkg.toml"


class Contract:
    def __init__(self, zed: Path, work_root: Path) -> None:
        self.zed = zed.resolve()
        self.root = work_root.resolve()
        self.registry = self.root / "registry"
        self.homes = self.root / "zed-homes"
        self.git_home = self.root / "git-home"
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
            self.git_home,
            self.repos,
            self.runs,
            self.evidence,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("", encoding="utf-8")

    def env(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        for key in (
            "GH_TOKEN",
            "GITHUB_TOKEN",
            "ZED_PKG_TOKEN",
            "ZED_PKG_REGISTRY",
            "ZED_PKG_HOME",
        ):
            env.pop(key, None)
        env.update(
            {
                "CI": "true",
                "HOME": str(self.git_home),
                "XDG_CONFIG_HOME": str(self.git_home / ".config"),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": "true",
                "GIT_AUTHOR_NAME": "Zed Metadata Contract",
                "GIT_AUTHOR_EMAIL": "zed-metadata-contract@example.invalid",
                "GIT_COMMITTER_NAME": "Zed Metadata Contract",
                "GIT_COMMITTER_EMAIL": "zed-metadata-contract@example.invalid",
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "protocol.file.allow",
                "GIT_CONFIG_VALUE_0": "always",
                "PYTHONDONTWRITEBYTECODE": "1",
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
        should_fail: bool = False,
        extra_env: Mapping[str, str] | None = None,
    ) -> str:
        command = [str(value) for value in argv]
        shown = " ".join(json.dumps(value) for value in command)
        self.log(f"\n$ (cd {cwd or Path.cwd()} && {shown})")
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=self.env(extra_env),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if completed.stdout:
            self.log(completed.stdout)
        if should_fail:
            if completed.returncode == 0:
                raise AssertionError(f"command unexpectedly succeeded: {shown}")
        elif completed.returncode != 0:
            raise RuntimeError(
                f"command failed with exit code {completed.returncode}: {shown}\n"
                f"{completed.stdout}"
            )
        return completed.stdout

    def git(self, cwd: Path, *args: str) -> str:
        return self.run(["git", "-C", cwd, *args], cwd=cwd)

    def zed_cmd(
        self,
        cwd: Path,
        home_name: str,
        *args: str,
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
            cwd=cwd,
            should_fail=should_fail,
        )

    def init_repo(self, path: Path) -> None:
        path.mkdir(parents=True)
        self.run(["git", "init", "-b", "main", path], cwd=self.root)
        self.git(path, "config", "user.name", "Zed Metadata Contract")
        self.git(path, "config", "user.email", "zed-metadata-contract@example.invalid")

    def commit_all(self, path: Path, message: str) -> str:
        self.git(path, "add", "-A")
        self.git(path, "commit", "-m", message)
        return self.git(path, "rev-parse", "HEAD").strip()

    def clone(
        self,
        source: Path,
        destination: Path,
        *,
        checkout_symlinks: bool = True,
    ) -> None:
        command: list[str | Path] = ["git"]
        if not checkout_symlinks:
            command.extend(["-c", "core.symlinks=false"])
        command.extend(["clone", "--no-recurse-submodules", source, destination])
        self.run(command)
        self.git(destination, "config", "user.name", "Zed Metadata Contract")
        self.git(
            destination,
            "config",
            "user.email",
            "zed-metadata-contract@example.invalid",
        )

    def build_regular_source(self) -> Path:
        child = self.repos / "child"
        self.init_repo(child)
        (child / "lib.txt").write_text("child-runtime\n", encoding="utf-8")
        self.commit_all(child, "child")

        root = self.repos / "regular-root"
        self.init_repo(root)
        (root / MANIFEST).write_text(
            "[package]\n"
            'org = "acme"\n'
            'name = "metadata-root"\n'
            'version = "1.2.3"\n\n'
            "[package.repository]\n"
            'vcs = "git"\n'
            'url = "https://example.invalid/acme/metadata-root.git"\n',
            encoding="utf-8",
        )
        (root / "root.txt").write_text("root-runtime\n", encoding="utf-8")
        self.run(
            [
                "git",
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
        self.commit_all(root, "root with child")
        return root

    def build_symlink_index_source(self, regular_source: Path) -> Path:
        source = self.repos / "symlink-index-root"
        self.clone(regular_source, source)
        gitmodules = source / ".gitmodules"
        actual = source / "actual-gitmodules"
        actual.write_text(gitmodules.read_text(encoding="utf-8"), encoding="utf-8")
        gitmodules.unlink()
        os.symlink("actual-gitmodules", gitmodules)
        self.commit_all(source, "commit symlinked Git metadata")
        mode = self.git(source, "ls-files", "--stage", "--", ".gitmodules")
        assert mode.startswith("120000 "), mode
        return source

    @staticmethod
    def assert_no_install_state(project: Path) -> None:
        assert not (project / ".zpkg.lock").exists()
        assert not (project / "zed_modules").exists()
        assert not (project / ".zpkg-staging").exists()

    @staticmethod
    def assert_no_archives(path: Path) -> None:
        archives = sorted(path.rglob("*.tar.gz")) if path.exists() else []
        assert not archives, f"failed command left archives: {archives}"

    def certify_worktree_symlink(self, source: Path) -> None:
        project = self.runs / "worktree-symlink"
        self.clone(source, project)
        gitmodules = project / ".gitmodules"
        external = self.runs / "external-gitmodules"
        external.write_text(gitmodules.read_text(encoding="utf-8"), encoding="utf-8")
        gitmodules.unlink()
        os.symlink(external, gitmodules)

        output = self.zed_cmd(
            project,
            "worktree-symlink",
            "install",
            "--git-submodules",
            should_fail=True,
        )
        assert "must be a regular file" in output.lower(), output
        self.assert_no_install_state(project)
        self.checks.append("worktree symlink fails before sync or install mutation")

    def certify_worktree_directory(self, source: Path) -> None:
        project = self.runs / "worktree-directory"
        self.clone(source, project)
        gitmodules = project / ".gitmodules"
        gitmodules.unlink()
        gitmodules.mkdir()

        out = self.runs / "worktree-directory-pack"
        output = self.zed_cmd(
            project,
            "worktree-directory",
            "pack",
            "--out",
            out,
            should_fail=True,
        )
        assert "must be a regular file" in output.lower(), output
        self.assert_no_archives(out)
        self.checks.append("worktree directory fails before archive creation")

    def certify_committed_symlink_mode(self, source: Path) -> None:
        project = self.runs / "index-symlink"
        self.clone(source, project, checkout_symlinks=False)
        gitmodules = project / ".gitmodules"
        assert gitmodules.is_file() and not gitmodules.is_symlink()
        mode = self.git(project, "ls-files", "--stage", "--", ".gitmodules")
        assert mode.startswith("120000 "), mode

        out = self.runs / "index-symlink-pack"
        output = self.zed_cmd(
            project,
            "index-symlink",
            "pack",
            "--out",
            out,
            should_fail=True,
        )
        assert "regular git blob" in output.lower(), output
        self.assert_no_archives(out)
        self.checks.append("committed symlink mode fails even when checkout is a regular file")

    def finish(self) -> None:
        version = self.run([self.zed, "--version"]).strip()
        record = {
            "schema": "zed.gitmodules-metadata-evidence/v1",
            "zed_version": version,
            "zed_sha256": hashlib.sha256(self.zed.read_bytes()).hexdigest(),
            "checks": self.checks,
            "network_credentials": False,
            "public_registry_mutation": False,
        }
        (self.evidence / "evidence.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        assert len(self.checks) == 3, self.checks
        self.log("\ncertified 3 indirect Git-submodule metadata checks")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    contract = Contract(args.zed, args.work_root)
    regular = contract.build_regular_source()
    symlink_index = contract.build_symlink_index_source(regular)
    contract.certify_worktree_symlink(regular)
    contract.certify_worktree_directory(regular)
    contract.certify_committed_symlink_mode(symlink_index)
    contract.finish()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f"Git-submodule metadata contract failed: {error}", file=sys.stderr)
        raise
