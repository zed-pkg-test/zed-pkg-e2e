#!/usr/bin/env python3
"""Black-box certification for the .gitmodules metadata trust boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Sequence

ZERO_OID = "0" * 40


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
        stdin: str | None = None,
    ) -> str:
        command = [str(value) for value in argv]
        shown = " ".join(json.dumps(value) for value in command)
        self.log(f"\n$ (cd {cwd or Path.cwd()} && {shown})")
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=self.env(),
            input=stdin,
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

    def git(self, cwd: Path, *args: str, stdin: str | None = None) -> str:
        return self.run(["git", "-C", cwd, *args], cwd=cwd, stdin=stdin)

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

    def commit_all(self, path: Path, message: str) -> None:
        self.git(path, "add", "-A")
        self.git(path, "commit", "-m", message)

    def build_fixture(self) -> Path:
        child = self.repos / "child"
        self.init_repo(child)
        (child / ".zpkg.toml").write_text(
            """[package]
org = "acme"
name = "metadata-child"
version = "1.0.0"

[package.repository]
vcs = "git"
url = "https://example.invalid/acme/metadata-child.git"
""",
            encoding="utf-8",
        )
        (child / "child.txt").write_text("child-runtime\n", encoding="utf-8")
        self.commit_all(child, "child")

        root = self.repos / "root"
        self.init_repo(root)
        (root / ".zpkg.toml").write_text(
            """[package]
org = "acme"
name = "metadata-root"
version = "1.0.0"

[package.repository]
vcs = "git"
url = "https://example.invalid/acme/metadata-root.git"
""",
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

    def clone(self, source: Path, name: str) -> Path:
        destination = self.runs / name
        self.run(
            ["git", "clone", "--no-recurse-submodules", source, destination],
            cwd=self.root,
        )
        self.git(destination, "config", "user.name", "Zed Metadata Contract")
        self.git(
            destination,
            "config",
            "user.email",
            "zed-metadata-contract@example.invalid",
        )
        return destination

    @staticmethod
    def assert_no_archives(path: Path) -> None:
        archives = sorted(path.rglob("*.tar.gz")) if path.exists() else []
        assert not archives, f"failed command left package archives: {archives}"

    @staticmethod
    def assert_metadata_error(output: str) -> None:
        lowered = output.lower()
        assert any(
            phrase in lowered
            for phrase in (
                "must be a regular file",
                "stage-zero regular git blob",
                "indirect git submodule metadata",
            )
        ), output

    def certify_regular_baseline(self, source: Path) -> None:
        project = self.clone(source, "regular-baseline")
        self.zed_cmd(project, "regular-baseline", "install", "--git-submodules")
        assert (project / "vendor/child/child.txt").is_file()
        stage = self.git(project, "ls-files", "--stage", "--", ".gitmodules")
        assert stage.startswith("100644 ") or stage.startswith("100755 "), stage
        assert " 0\t.gitmodules" in stage, stage
        self.checks.append("regular stage-zero .gitmodules permits recursive install")

    def replace_with_symlink(self, project: Path) -> Path:
        metadata = project / ".gitmodules"
        target = project / "reviewed-gitmodules"
        target.write_bytes(metadata.read_bytes())
        metadata.unlink()
        os.symlink(target.name, metadata)
        assert metadata.is_symlink()
        return target

    def certify_symlink_pack_rejection(self, source: Path) -> None:
        project = self.clone(source, "symlink-pack")
        target = self.replace_with_symlink(project)
        before = target.read_bytes()
        output_dir = self.runs / "symlink-pack-output"
        output = self.zed_cmd(
            project,
            "symlink-pack",
            "pack",
            "--out",
            output_dir,
            should_fail=True,
        )
        self.assert_metadata_error(output)
        self.assert_no_archives(output_dir)
        assert target.read_bytes() == before
        self.checks.append("symlinked .gitmodules rejects pack before archive creation")

    def certify_symlink_install_rejection(self, source: Path) -> None:
        project = self.clone(source, "symlink-install")
        target = self.replace_with_symlink(project)
        before = target.read_bytes()
        output = self.zed_cmd(
            project,
            "symlink-install",
            "install",
            "--git-submodules",
            should_fail=True,
        )
        self.assert_metadata_error(output)
        assert not (project / "vendor/child/.git").exists()
        assert not (project / ".zpkg.lock").exists()
        assert target.read_bytes() == before
        self.checks.append("symlinked .gitmodules rejects install before checkout or lock mutation")

    def certify_directory_pack_rejection(self, source: Path) -> None:
        project = self.clone(source, "directory-pack")
        metadata = project / ".gitmodules"
        metadata.unlink()
        metadata.mkdir()
        (metadata / "payload").write_text("indirect\n", encoding="utf-8")
        output_dir = self.runs / "directory-pack-output"
        output = self.zed_cmd(
            project,
            "directory-pack",
            "pack",
            "--out",
            output_dir,
            should_fail=True,
        )
        self.assert_metadata_error(output)
        self.assert_no_archives(output_dir)
        self.checks.append("directory .gitmodules rejects pack before Git parsing")

    def certify_gitlink_index_rejection(self, source: Path) -> None:
        project = self.clone(source, "gitlink-index")
        head = self.git(project, "rev-parse", "HEAD").strip()
        self.git(
            project,
            "update-index",
            "--add",
            "--cacheinfo",
            "160000",
            head,
            ".gitmodules",
        )
        stage = self.git(project, "ls-files", "--stage", "--", ".gitmodules")
        assert stage.startswith("160000 "), stage

        output_dir = self.runs / "gitlink-index-output"
        output = self.zed_cmd(
            project,
            "gitlink-index",
            "pack",
            "--out",
            output_dir,
            should_fail=True,
        )
        self.assert_metadata_error(output)
        self.assert_no_archives(output_dir)
        self.checks.append("gitlink-indexed .gitmodules rejects pack before archive creation")

    def certify_conflict_stage_rejection(self, source: Path) -> None:
        project = self.clone(source, "conflict-stage")
        blob = self.git(project, "hash-object", ".gitmodules").strip()
        records = (
            f"0 {ZERO_OID}\t.gitmodules\n"
            f"100644 {blob} 1\t.gitmodules\n"
            f"100644 {blob} 2\t.gitmodules\n"
            f"100644 {blob} 3\t.gitmodules\n"
        )
        self.git(project, "update-index", "--index-info", stdin=records)
        unmerged = self.git(project, "ls-files", "--unmerged", "--", ".gitmodules")
        assert " 1\t.gitmodules" in unmerged, unmerged
        assert " 2\t.gitmodules" in unmerged, unmerged
        assert " 3\t.gitmodules" in unmerged, unmerged

        output = self.zed_cmd(
            project,
            "conflict-stage",
            "install",
            "--git-submodules",
            should_fail=True,
        )
        self.assert_metadata_error(output)
        assert not (project / "vendor/child/.git").exists()
        assert not (project / ".zpkg.lock").exists()
        self.checks.append("conflicted .gitmodules index rejects install before mutation")

    def finish(self) -> None:
        version = self.run([self.zed, "--version"]).strip()
        evidence = {
            "schema": "zed.gitmodules-metadata-evidence/v1",
            "zed_version": version,
            "zed_sha256": hashlib.sha256(self.zed.read_bytes()).hexdigest(),
            "checks": self.checks,
            "network_credentials": False,
            "public_registry_mutation": False,
        }
        (self.evidence / "evidence.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        assert len(self.checks) == 6, self.checks
        self.log(f"\ncertified {len(self.checks)} .gitmodules metadata checks")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    contract = Contract(args.zed, args.work_root)
    source = contract.build_fixture()
    contract.certify_regular_baseline(source)
    contract.certify_symlink_pack_rejection(source)
    contract.certify_symlink_install_rejection(source)
    contract.certify_directory_pack_rejection(source)
    contract.certify_gitlink_index_rejection(source)
    contract.certify_conflict_stage_rejection(source)
    contract.finish()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f".gitmodules metadata contract failed: {error}", file=sys.stderr)
        raise
