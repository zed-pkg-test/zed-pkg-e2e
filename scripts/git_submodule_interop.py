#!/usr/bin/env python3
"""Credential-free black-box certification for Zed/Git-submodule interop."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
                "GIT_AUTHOR_NAME": "Zed Contract",
                "GIT_AUTHOR_EMAIL": "zed-contract@example.invalid",
                "GIT_COMMITTER_NAME": "Zed Contract",
                "GIT_COMMITTER_EMAIL": "zed-contract@example.invalid",
                # Git rejects local-path submodule transports by default. This
                # one-process override keeps every fixture local and explicit.
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "protocol.file.allow",
                "GIT_CONFIG_VALUE_0": "always",
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
        self.git(adopted, "commit", "-m", "adopt submodule into Zed authority")

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
        self.git(frozen, "commit", "-m", "change submodule branch metadata")
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

    def build_mixed_fixture(self) -> Path:
        zed_child = self.repos / "mixed-client"
        self.init_repo(zed_child)
        self.write_package(zed_child, org="acme", name="mixed-client")
        (zed_child / "lib.txt").write_text("mixed Zed package\n", encoding="utf-8")
        self.commit_all(zed_child, "mixed Zed package")

        legacy_child = self.repos / "legacy-docs"
        self.init_repo(legacy_child)
        (legacy_child / "README.md").write_text(
            "ordinary Git submodule\n", encoding="utf-8"
        )
        self.commit_all(legacy_child, "legacy documentation")

        root = self.repos / "mixed-root"
        self.init_repo(root)
        self.write_package(root, org="acme", name="mixed-root")
        self.add_submodule(root, zed_child, "vendor/client")
        self.add_submodule(root, legacy_child, "vendor/legacy")
        self.commit_all(root, "mixed Git and Zed submodules")
        return root

    def certify_mixed_repository_contract(self) -> None:
        root_source = self.build_mixed_fixture()
        adopted = self.runs / "mixed-adopted"
        self.clone_no_submodules(root_source, adopted)
        output = self.zed_cmd(
            adopted,
            "mixed-adopted",
            "overtake",
            "--git-submodules",
        )
        assert "overtook 1 Git submodule package(s)" in output
        assert "left 1 non-Zed submodule(s) under Git authority" in output
        assert "vendor/legacy" in output

        manifest_before_commit = (adopted / MANIFEST).read_bytes()
        manifest = tomllib.loads(manifest_before_commit.decode("utf-8"))
        assert manifest["dependencies"]["acme/mixed-client"] == "=1.2.3"
        assert "vendor/client" in manifest["workspace"]["members"]
        assert "vendor/legacy" not in manifest["workspace"]["members"]

        lock_before_commit = (adopted / LOCKFILE).read_bytes()
        lock = tomllib.loads(lock_before_commit.decode("utf-8"))
        entries = lock.get("git-submodule")
        assert isinstance(entries, list) and len(entries) == 1
        assert entries[0]["package"] == "acme/mixed-client"
        assert entries[0]["path"] == "vendor/client"
        assert "vendor/legacy" not in lock_before_commit.decode("utf-8")
        assert (adopted / "zed_modules/acme/mixed-client/lib.txt").is_file()
        assert (adopted / "vendor/legacy/README.md").is_file()
        self.checks.append("mixed takeover adopts only explicit Zed packages")

        self.git(adopted, "add", MANIFEST, LOCKFILE)
        self.git(adopted, "commit", "-m", "adopt only the Zed submodule")

        frozen = self.runs / "mixed-frozen"
        self.clone_no_submodules(adopted, frozen)
        manifest_before = (frozen / MANIFEST).read_bytes()
        lock_before = (frozen / LOCKFILE).read_bytes()
        assert not (frozen / "vendor/client/lib.txt").exists()
        assert not (frozen / "vendor/legacy/README.md").exists()

        self.zed_cmd(
            frozen,
            "mixed-frozen",
            "install",
            "--frozen",
            extra_env={"ZED_PKG_GIT_SUBMODULES": "yes"},
        )
        assert (frozen / MANIFEST).read_bytes() == manifest_before
        assert (frozen / LOCKFILE).read_bytes() == lock_before
        assert (frozen / "vendor/client/lib.txt").read_text(
            encoding="utf-8"
        ) == "mixed Zed package\n"
        assert (frozen / "vendor/legacy/README.md").read_text(
            encoding="utf-8"
        ) == "ordinary Git submodule\n"
        assert (frozen / "zed_modules/acme/mixed-client/lib.txt").read_text(
            encoding="utf-8"
        ) == "mixed Zed package\n"
        recursive_status = self.git(frozen, "submodule", "status", "--recursive")
        for line in recursive_status.splitlines():
            if line:
                assert line[0] not in "-+U", recursive_status
        self.checks.append(
            "mixed frozen replay restores adopted and Git-only transports byte-exactly"
        )

        (self.evidence / "mixed-manifest.toml").write_bytes(manifest_before_commit)
        (self.evidence / "mixed-lock.toml").write_bytes(lock_before_commit)

    def certify_git_only_boundary(self) -> None:
        legacy_child = self.repos / "git-only-child"
        self.init_repo(legacy_child)
        (legacy_child / "README.md").write_text(
            "ordinary Git submodule\n", encoding="utf-8"
        )
        self.commit_all(legacy_child, "Git-only child")

        root = self.runs / "git-only-root"
        self.init_repo(root)
        original = self.write_package(root, org="acme", name="git-only-root")
        self.add_submodule(root, legacy_child, "vendor/legacy")
        self.commit_all(root, "Git-only submodule root")
        self.git(
            root,
            "submodule",
            "deinit",
            "--force",
            "--",
            "vendor/legacy",
        )
        assert not (root / "vendor/legacy/README.md").exists()

        output = self.zed_cmd(
            root,
            "git-only",
            "overtake",
            "--git-submodules",
            should_fail=True,
        )
        assert "no overtake-compatible Zed submodules" in output
        assert "vendor/legacy" in output
        assert (root / MANIFEST).read_bytes() == original
        self.assert_no_install_state(root)
        assert (root / "vendor/legacy/README.md").read_text(
            encoding="utf-8"
        ) == "ordinary Git submodule\n"
        self.checks.append(
            "Git-only takeover synchronizes transport without publishing Zed state"
        )

    def certify_invalid_manifest_boundary(self) -> None:
        invalid_child = self.repos / "invalid-manifest-child"
        self.init_repo(invalid_child)
        (invalid_child / MANIFEST).write_text(
            "[package]\nname = [this is not valid TOML\n",
            encoding="utf-8",
        )
        self.commit_all(invalid_child, "invalid package intent")

        root = self.runs / "invalid-manifest-root"
        self.init_repo(root)
        original = self.write_package(root, org="acme", name="invalid-root")
        self.add_submodule(root, invalid_child, "vendor/invalid")
        self.commit_all(root, "invalid package submodule root")

        output = self.zed_cmd(
            root,
            "invalid-manifest",
            "overtake",
            "--git-submodules",
            should_fail=True,
        )
        assert "contains an invalid .zpkg.toml" in output
        assert (root / MANIFEST).read_bytes() == original
        self.assert_no_install_state(root)
        self.checks.append("invalid package intent fails closed before Zed mutation")

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
            "schema": "zed.git-submodule-interop-evidence/v2",
            "zed_version": version,
            "zed_sha256": zed_sha256,
            "checks": self.checks,
            "network_credentials": False,
            "public_registry_mutation": False,
        }
        (self.evidence / "evidence.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        assert len(self.checks) == 12, self.checks
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
    contract.certify_mixed_repository_contract()
    contract.certify_git_only_boundary()
    contract.certify_invalid_manifest_boundary()
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
