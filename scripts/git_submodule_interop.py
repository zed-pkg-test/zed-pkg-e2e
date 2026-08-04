#!/usr/bin/env python3
"""Credential-free black-box certification for Zed/Git-submodule interop."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Mapping, Sequence

MANIFEST = ".zpkg.toml"
LOCKFILE = ".zpkg.lock"
STAGING = ".zpkg-staging"


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

    def env(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "CI": "true",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": "true",
                "GIT_AUTHOR_NAME": "Zed Contract",
                "GIT_AUTHOR_EMAIL": "zed-contract@example.invalid",
                "GIT_COMMITTER_NAME": "Zed Contract",
                "GIT_COMMITTER_EMAIL": "zed-contract@example.invalid",
                # Git rejects local-path submodule transports by default. This
                # one-process override keeps every fixture local and explicit.
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "protocol.file.allow",
                "GIT_CONFIG_VALUE_0": "always",
                "ZED_PKG_TOKEN": "",
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

    def git(self, cwd: Path, *args: str, should_fail: bool = False) -> str:
        return self.run(["git", "-C", cwd, *args], cwd=cwd, should_fail=should_fail)

    def zed_cmd(
        self,
        cwd: Path,
        home_name: str,
        *args: str,
        should_fail: bool = False,
        extra_env: Mapping[str, str] | None = None,
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
            extra_env=extra_env,
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
    def write_package(
        path: Path,
        *,
        org: str,
        name: str,
        version: str = "1.2.3",
        dependency: str | None = None,
    ) -> bytes:
        text = (
            f'[package]\norg = "{org}"\nname = "{name}"\n'
            f'version = "{version}"\n\n'
            "[package.repository]\n"
            'vcs = "git"\n'
            f'url = "https://example.invalid/{org}/{name}.git"\n'
        )
        if dependency:
            text += f'\n[dependencies]\n"{dependency}" = "=1.0.0"\n'
        data = text.encode("utf-8")
        (path / MANIFEST).write_bytes(data)
        return data

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
        self.git(destination, "config", "user.name", "Zed Contract")
        self.git(destination, "config", "user.email", "zed-contract@example.invalid")

    @staticmethod
    def assert_no_install_state(project: Path) -> None:
        assert not (project / LOCKFILE).exists(), "failed takeover left a lockfile"
        assert not (project / "zed_modules").exists(), "failed takeover left modules"
        assert not (project / STAGING).exists(), "failed takeover left staging state"

    def build_fixture_graph(self) -> tuple[Path, Path, Path, str]:
        schema = self.repos / "schema"
        self.init_repo(schema)
        (schema / "schema.txt").write_text("schema-v1\n", encoding="utf-8")
        self.commit_all(schema, "schema")

        child = self.repos / "client"
        self.init_repo(child)
        self.write_package(child, org="acme", name="client")
        (child / "lib.txt").write_text("hello from client\n", encoding="utf-8")
        self.add_submodule(child, schema, "vendor/schema")
        child_commit = self.commit_all(child, "client with nested schema")

        root = self.repos / "root"
        self.init_repo(root)
        self.write_package(root, org="acme", name="root")
        self.add_submodule(root, child, "vendor/client")
        self.commit_all(root, "root with client")
        return schema, child, root, child_commit

    def certify_cooperative_install(self, root_source: Path) -> None:
        disabled = self.runs / "disabled"
        self.clone_no_submodules(root_source, disabled)
        assert not (disabled / "vendor/client" / MANIFEST).exists()
        self.zed_cmd(
            disabled,
            "disabled",
            "install",
            "--git-submodules=false",
            extra_env={"ZED_PKG_GIT_SUBMODULES": "1"},
        )
        assert not (disabled / "vendor/client" / MANIFEST).exists(), (
            "explicit false did not override inherited submodule mode"
        )
        self.checks.append("explicit false overrides inherited submodule mode")

        cooperative = self.runs / "cooperative"
        self.clone_no_submodules(root_source, cooperative)
        self.zed_cmd(cooperative, "cooperative", "install", "--git-submodules")
        assert (cooperative / "vendor/client" / "lib.txt").is_file()
        assert (
            cooperative / "vendor/client/vendor/schema/schema.txt"
        ).read_text(encoding="utf-8") == "schema-v1\n"
        self.checks.append("cooperative install initializes recursive submodules")

    def certify_takeover_and_frozen_replay(
        self, root_source: Path, child_commit: str
    ) -> None:
        adopted = self.runs / "adopted"
        self.clone_no_submodules(root_source, adopted)
        self.zed_cmd(adopted, "adopted", "overtake", "--git-submodules")

        manifest = tomllib.loads((adopted / MANIFEST).read_text(encoding="utf-8"))
        assert manifest["dependencies"]["acme/client"] == "=1.2.3"
        assert "vendor/client" in manifest["workspace"]["members"]

        lock_bytes = (adopted / LOCKFILE).read_bytes()
        lock = tomllib.loads(lock_bytes.decode("utf-8"))
        entries = lock.get("git-submodule")
        assert isinstance(entries, list) and len(entries) == 1
        entry = entries[0]
        assert entry["path"] == "vendor/client"
        assert entry["package"] == "acme/client"
        assert entry["version"] == "1.2.3"
        assert entry["commit"] == child_commit
        assert len(entry["sha256"]) == 64
        int(entry["sha256"], 16)
        assert entry["size"] > 0
        assert (adopted / "zed_modules/acme/client/lib.txt").is_file()
        self.checks.append("takeover writes workspace, dependency, and Git provenance")

        self.git(adopted, "add", MANIFEST, LOCKFILE)
        self.git(adopted, "commit", "adopt submodule into Zed authority")

        frozen = self.runs / "frozen"
        self.clone_no_submodules(adopted, frozen)
        before = (frozen / LOCKFILE).read_bytes()
        self.zed_cmd(
            frozen,
            "frozen",
            "install",
            "--git-submodules",
            "--frozen",
        )
        assert (frozen / LOCKFILE).read_bytes() == before
        assert (frozen / "vendor/client/vendor/schema/schema.txt").is_file()
        assert (frozen / "zed_modules/acme/client/lib.txt").is_file()
        self.checks.append("fresh-clone frozen replay is lockfile-byte-stable")

        # Dirty content must fail before rewriting the immutable lock.
        (frozen / "vendor/client/lib.txt").write_text(
            "locally modified\n", encoding="utf-8"
        )
        self.zed_cmd(
            frozen,
            "frozen",
            "install",
            "--git-submodules",
            "--frozen",
            should_fail=True,
        )
        assert (frozen / LOCKFILE).read_bytes() == before
        self.git(frozen / "vendor/client", "reset", "--hard", "HEAD")
        self.checks.append("dirty submodule drift is rejected without lock mutation")

        # A committed .gitmodules branch change is transport-provenance drift.
        self.git(
            frozen,
            "config",
            "-f",
            ".gitmodules",
            "submodule.vendor/client.branch",
            "alternate",
        )
        self.git(frozen, "add", ".gitmodules")
        self.git(frozen, "commit", "change submodule branch metadata")
        self.zed_cmd(
            frozen,
            "frozen",
            "install",
            "--git-submodules",
            "--frozen",
            should_fail=True,
        )
        assert (frozen / LOCKFILE).read_bytes() == before
        self.checks.append("committed branch drift is rejected without lock mutation")

        (self.evidence / "adopted-lock.toml").write_bytes(lock_bytes)

    def build_broken_child(self) -> Path:
        broken = self.repos / "broken-client"
        self.init_repo(broken)
        self.write_package(
            broken,
            org="acme",
            name="broken-client",
            dependency="acme/missing",
        )
        (broken / "lib.txt").write_text("broken dependency graph\n", encoding="utf-8")
        self.commit_all(broken, "client with unresolved dependency")
        return broken

    def certify_failure_atomic_takeover(self, broken_child: Path) -> None:
        existing = self.runs / "rollback-existing"
        self.init_repo(existing)
        original = (
            b"# preserve these exact bytes on failure\n"
            b"[package]\n"
            b'org = "acme"\n'
            b'name = "rollback-root"\n'
            b'version = "1.2.3"\n\n'
            b"[package.repository]\n"
            b'vcs = "git"\n'
            b'url = "https://example.invalid/acme/rollback-root.git"\n'
        )
        (existing / MANIFEST).write_bytes(original)
        self.add_submodule(existing, broken_child, "vendor/client")
        self.commit_all(existing, "rollback root")
        output = self.zed_cmd(
            existing,
            "rollback-existing",
            "overtake",
            "--git-submodules",
            should_fail=True,
        )
        assert "restored the prior root manifest" in output
        assert (existing / MANIFEST).read_bytes() == original
        self.assert_no_install_state(existing)
        self.checks.append("failed takeover restores exact authored manifest bytes")

        generated = self.runs / "rollback-generated"
        self.init_repo(generated)
        self.add_submodule(generated, broken_child, "vendor/client")
        self.commit_all(generated, "manifestless rollback root")
        output = self.zed_cmd(
            generated,
            "rollback-generated",
            "overtake",
            "--git-submodules",
            should_fail=True,
        )
        assert "restored the prior root manifest" in output
        assert not (generated / MANIFEST).exists()
        self.assert_no_install_state(generated)
        self.checks.append("failed takeover removes a generated root manifest")

    def finish(self) -> None:
        version = self.run([self.zed, "--version"]).strip()
        zed_sha256 = hashlib.sha256(self.zed.read_bytes()).hexdigest()
        record = {
            "schema": "zed.git-submodule-interop-evidence/v1",
            "zed_version": version,
            "zed_sha256": zed_sha256,
            "checks": self.checks,
            "network_credentials": False,
            "public_registry_mutation": False,
        }
        (self.evidence / "evidence.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        assert len(self.checks) == 8, self.checks
        self.log(f"\ncertified {len(self.checks)} Git-submodule interoperability checks")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    contract = Contract(args.zed, args.work_root)
    _, _, root, child_commit = contract.build_fixture_graph()
    contract.certify_cooperative_install(root)
    contract.certify_takeover_and_frozen_replay(root, child_commit)
    broken = contract.build_broken_child()
    contract.certify_failure_atomic_takeover(broken)
    contract.finish()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f"git-submodule interoperability contract failed: {error}", file=sys.stderr)
        raise
