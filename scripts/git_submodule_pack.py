#!/usr/bin/env python3
"""Credential-free archive certification for Zed/Git-submodule packaging."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath
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
                "GIT_AUTHOR_NAME": "Zed Pack Contract",
                "GIT_AUTHOR_EMAIL": "zed-pack-contract@example.invalid",
                "GIT_COMMITTER_NAME": "Zed Pack Contract",
                "GIT_COMMITTER_EMAIL": "zed-pack-contract@example.invalid",
                # Every fixture is local. Permit file transport only inside
                # the disposable contract process and never persist it.
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
        self.git(path, "config", "user.name", "Zed Pack Contract")
        self.git(path, "config", "user.email", "zed-pack-contract@example.invalid")

    def commit_all(self, path: Path, message: str) -> str:
        self.git(path, "add", "-A")
        self.git(path, "commit", "-m", message)
        return self.git(path, "rev-parse", "HEAD").strip()

    @staticmethod
    def write_manifest(
        path: Path,
        *,
        name: str,
        exclude: Sequence[str] = (),
    ) -> None:
        document = (
            "[package]\n"
            'org = "acme"\n'
            f'name = "{name}"\n'
            'version = "1.2.3"\n\n'
            "[package.repository]\n"
            'vcs = "git"\n'
            f'url = "https://example.invalid/acme/{name}.git"\n'
        )
        if exclude:
            quoted = ", ".join(json.dumps(pattern) for pattern in exclude)
            document += f"\n[publish]\nexclude = [{quoted}]\n"
        (path / MANIFEST).write_text(document, encoding="utf-8")

    def add_submodule(self, root: Path, source: Path, destination: str) -> None:
        self.run(
            [
                "git",
                "-C",
                root,
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "add",
                str(source),
                destination,
            ],
            cwd=root,
        )

    def clone_no_submodules(self, source: Path, destination: Path) -> None:
        self.run(["git", "clone", "--no-recurse-submodules", source, destination])
        self.git(destination, "config", "user.name", "Zed Pack Contract")
        self.git(destination, "config", "user.email", "zed-pack-contract@example.invalid")

    def build_child_graph(self) -> Path:
        schema = self.repos / "schema"
        self.init_repo(schema)
        (schema / "schema.txt").write_text("schema-v1\n", encoding="utf-8")
        self.commit_all(schema, "schema")

        child = self.repos / "client"
        self.init_repo(child)
        self.write_manifest(child, name="client")
        (child / "lib.txt").write_text("client-runtime\n", encoding="utf-8")
        self.add_submodule(child, schema, "vendor/schema")
        self.commit_all(child, "client with nested schema")
        return child

    def build_root(
        self,
        *,
        name: str,
        child: Path,
        exclude: Sequence[str] = (),
        zedignore: Sequence[str] = (),
    ) -> Path:
        root = self.repos / name
        self.init_repo(root)
        self.write_manifest(root, name=name, exclude=exclude)
        if zedignore:
            (root / ".zedignore").write_text(
                "\n".join(zedignore) + "\n", encoding="utf-8"
            )
        (root / "root.txt").write_text(f"{name}-runtime\n", encoding="utf-8")
        self.add_submodule(root, child, "vendor/client")
        self.commit_all(root, f"{name} with client")
        return root

    @staticmethod
    def assert_no_archives(path: Path) -> None:
        archives = sorted(path.rglob("*.tar.gz")) if path.exists() else []
        assert not archives, f"failed command left package archives: {archives}"

    @staticmethod
    def archive_entries(path: Path) -> set[str]:
        with tarfile.open(path, mode="r:gz") as archive:
            return {member.name for member in archive.getmembers() if member.isfile()}

    @staticmethod
    def assert_no_vcs_metadata(entries: set[str]) -> None:
        forbidden = {".git", ".gitmodules", ".hg", ".svn"}
        leaked = []
        for entry in entries:
            if forbidden.intersection(PurePosixPath(entry).parts):
                leaked.append(entry)
        assert not leaked, f"archive leaked VCS control data: {sorted(leaked)}"

    def certify_uninitialized_rejection(self, source: Path) -> None:
        project = self.runs / "uninitialized"
        self.clone_no_submodules(source, project)
        assert not (project / "vendor/client/.git").exists()

        pack_out = self.runs / "uninitialized-pack"
        output = self.zed_cmd(
            project,
            "uninitialized-pack",
            "pack",
            "--out",
            pack_out,
            should_fail=True,
        )
        assert "not initialized" in output.lower(), output
        self.assert_no_archives(pack_out)
        self.checks.append("uninitialized included submodule rejects pack before output")

        output = self.zed_cmd(
            project,
            "uninitialized-publish",
            "publish",
            "--dry-run",
            "--skip-vcs-checks",
            should_fail=True,
        )
        assert "not initialized" in output.lower(), output
        self.assert_no_archives(project / ".zed/pack")
        self.checks.append("uninitialized included submodule rejects publish dry-run")

    def certify_initialized_archive(self, source: Path) -> Path:
        project = self.runs / "initialized"
        self.clone_no_submodules(source, project)
        self.zed_cmd(project, "initialized", "install", "--git-submodules")

        assert (project / "vendor/client/lib.txt").is_file()
        assert (project / "vendor/client/vendor/schema/schema.txt").is_file()
        assert (project / "vendor/client/.git").is_file()
        assert (project / "vendor/client/vendor/schema/.git").is_file()
        self.checks.append("zed install recursively materializes package source")

        pack_out = self.runs / "initialized-pack"
        self.zed_cmd(project, "initialized", "pack", "--out", pack_out)
        archive = pack_out / "acme-included-root-1.2.3.tar.gz"
        assert archive.is_file(), f"expected archive missing: {archive}"
        entries = self.archive_entries(archive)
        assert "pkg/root.txt" in entries
        assert "pkg/vendor/client/lib.txt" in entries
        assert "pkg/vendor/client/vendor/schema/schema.txt" in entries
        self.checks.append("initialized pack embeds top-level and nested runtime source")

        self.assert_no_vcs_metadata(entries)
        self.checks.append("archive strips root and nested VCS control metadata")

        output = self.zed_cmd(
            project,
            "initialized",
            "publish",
            "--dry-run",
            "--skip-vcs-checks",
        )
        assert "dry run: would publish acme/included-root@1.2.3" in output, output
        self.checks.append("initialized publish dry-run reaches immutable package plan")
        return project

    def certify_dirty_rejection(self, project: Path) -> None:
        child_file = project / "vendor/client/lib.txt"
        child_file.write_text("dirty-client\n", encoding="utf-8")
        output = self.zed_cmd(
            project,
            "dirty-top",
            "pack",
            "--out",
            self.runs / "dirty-top-pack",
            should_fail=True,
        )
        assert "dirty" in output.lower(), output
        self.assert_no_archives(self.runs / "dirty-top-pack")
        self.git(project / "vendor/client", "reset", "--hard", "HEAD")
        self.checks.append("dirty included submodule rejects pack before output")

        nested_file = project / "vendor/client/vendor/schema/schema.txt"
        nested_file.write_text("dirty-schema\n", encoding="utf-8")
        output = self.zed_cmd(
            project,
            "dirty-nested",
            "pack",
            "--out",
            self.runs / "dirty-nested-pack",
            should_fail=True,
        )
        assert any(word in output.lower() for word in ("dirty", "drift")), output
        self.assert_no_archives(self.runs / "dirty-nested-pack")
        self.git(project / "vendor/client/vendor/schema", "reset", "--hard", "HEAD")
        self.checks.append("dirty nested submodule rejects pack before output")

    def certify_excluded_uninitialized(self, source: Path, *, home_name: str) -> set[str]:
        project = self.runs / home_name
        self.clone_no_submodules(source, project)
        assert not (project / "vendor/client/.git").exists()

        out = self.runs / f"{home_name}-pack"
        self.zed_cmd(project, home_name, "pack", "--out", out)
        archive = out / f"acme-{source.name}-1.2.3.tar.gz"
        assert archive.is_file(), f"expected archive missing: {archive}"
        entries = self.archive_entries(archive)
        assert not any(entry.startswith("pkg/vendor/client/") for entry in entries)
        self.assert_no_vcs_metadata(entries)
        return entries

    def certify_noncanonical_exclusion_rejected(
        self,
        source: Path,
        *,
        home_name: str,
        check: str,
    ) -> None:
        project = self.runs / home_name
        self.clone_no_submodules(source, project)
        assert not (project / "vendor/client/.git").exists()

        out = self.runs / f"{home_name}-pack"
        output = self.zed_cmd(
            project,
            home_name,
            "pack",
            "--out",
            out,
            should_fail=True,
        )
        assert "not initialized" in output.lower(), output
        self.assert_no_archives(out)
        self.checks.append(check)

    def certify_indirect_gitmodules_rejected(self, source: Path) -> None:
        symlink_project = self.runs / "symlinked-gitmodules"
        self.clone_no_submodules(source, symlink_project)
        gitmodules = symlink_project / ".gitmodules"
        external = self.runs / "external-gitmodules"
        external.write_text(gitmodules.read_text(encoding="utf-8"), encoding="utf-8")
        gitmodules.unlink()
        os.symlink(external, gitmodules)

        output = self.zed_cmd(
            symlink_project,
            "symlinked-gitmodules",
            "install",
            "--git-submodules",
            should_fail=True,
        )
        assert "must be a regular file" in output.lower(), output
        assert not (symlink_project / ".zpkg.lock").exists()
        assert not (symlink_project / "zed_modules").exists()
        self.checks.append("symlinked .gitmodules fails before sync or lock mutation")

        directory_project = self.runs / "directory-gitmodules"
        self.clone_no_submodules(source, directory_project)
        gitmodules = directory_project / ".gitmodules"
        gitmodules.unlink()
        gitmodules.mkdir()

        out = self.runs / "directory-gitmodules-pack"
        output = self.zed_cmd(
            directory_project,
            "directory-gitmodules",
            "pack",
            "--out",
            out,
            should_fail=True,
        )
        assert "must be a regular file" in output.lower(), output
        self.assert_no_archives(out)
        self.checks.append("directory .gitmodules fails before archive creation")

    def finish(self) -> None:
        version = self.run([self.zed, "--version"]).strip()
        record = {
            "schema": "zed.git-submodule-pack-evidence/v1",
            "zed_version": version,
            "zed_sha256": hashlib.sha256(self.zed.read_bytes()).hexdigest(),
            "checks": self.checks,
            "network_credentials": False,
            "public_registry_mutation": False,
        }
        (self.evidence / "evidence.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        assert len(self.checks) == 14, self.checks
        self.log(f"\ncertified {len(self.checks)} Git-submodule packaging checks")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    contract = Contract(args.zed, args.work_root)
    child = contract.build_child_graph()
    included = contract.build_root(name="included-root", child=child)
    excluded = contract.build_root(
        name="excluded-root",
        child=child,
        exclude=["vendor/client/**"],
    )
    ignored = contract.build_root(
        name="ignored-root",
        child=child,
        zedignore=["vendor/client/**"],
    )
    dot_excluded = contract.build_root(
        name="dot-excluded-root",
        child=child,
        exclude=["./vendor/client/**"],
    )
    absolute_excluded = contract.build_root(
        name="absolute-excluded-root",
        child=child,
        exclude=["/vendor/client/**"],
    )

    contract.certify_uninitialized_rejection(included)
    contract.certify_indirect_gitmodules_rejected(included)
    initialized = contract.certify_initialized_archive(included)
    contract.certify_dirty_rejection(initialized)
    contract.certify_excluded_uninitialized(excluded, home_name="publish-excluded")
    contract.checks.append(
        "publish.exclude permits an omitted, uninitialized submodule subtree"
    )
    contract.certify_excluded_uninitialized(ignored, home_name="zedignore-excluded")
    contract.checks.append(".zedignore permits an omitted, uninitialized submodule subtree")
    contract.certify_noncanonical_exclusion_rejected(
        dot_excluded,
        home_name="dot-exclusion-rejected",
        check="dot-prefixed recursive exclusion cannot bypass initialization",
    )
    contract.certify_noncanonical_exclusion_rejected(
        absolute_excluded,
        home_name="absolute-exclusion-rejected",
        check="leading-slash recursive exclusion cannot bypass initialization",
    )
    contract.finish()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f"Git-submodule pack contract failed: {error}", file=sys.stderr)
        raise
