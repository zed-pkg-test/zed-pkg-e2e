#!/usr/bin/env python3
"""Black-box canary for `zed fetch --frozen`.

The test publishes one real zed-pkg-test fixture to a private file registry,
creates a consumer lock through normal installation, uninstalls the project
materialization, and then proves the resolver-only bundle contract from the
built CLI under test. All state is disposable and no credentials are used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Mapping, Sequence


def shell_quote(value: str) -> str:
    if not value:
        return "''"
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", value):
        return value
    return "'" + value.replace("'", "'\\''") + "'"


def run(
    command: Sequence[str | Path],
    *,
    cwd: Path,
    extra_env: Mapping[str, str] | None = None,
    should_fail: bool = False,
) -> str:
    argv = [str(value) for value in command]
    print(f"\n$ (cd {cwd} && {' '.join(shell_quote(value) for value in argv)})", flush=True)
    environment = os.environ.copy()
    environment.update(
        {
            "CI": "true",
            "GIT_TERMINAL_PROMPT": "0",
            "ZED_PKG_TOKEN": "",
            "ZED_PKG_INTERACTIVE": "false",
        }
    )
    if extra_env:
        environment.update(extra_env)
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    print(completed.stdout, end="", flush=True)
    if should_fail:
        if completed.returncode == 0:
            raise AssertionError(f"command unexpectedly succeeded: {argv!r}")
    elif completed.returncode != 0:
        raise RuntimeError(
            f"command failed with exit code {completed.returncode}: {argv!r}\n{completed.stdout}"
        )
    return completed.stdout


def tree_digest(root: Path) -> tuple[tuple[str, int, str], ...]:
    if not root.exists():
        return ()
    rows: list[tuple[str, int, str]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            rows.append((relative, -1, f"symlink:{os.readlink(path)}"))
        elif path.is_file():
            data = path.read_bytes()
            rows.append((relative, len(data), hashlib.sha256(data).hexdigest()))
    return tuple(rows)


def assert_git_clean(root: Path) -> None:
    output = run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=root,
    )
    if output.strip():
        raise AssertionError(f"fixture checkout was mutated:\n{output}")


def assert_no_fetch_temporaries(parent: Path) -> None:
    escaped = sorted(
        path
        for path in parent.iterdir()
        if path.name.startswith(".zed-fetch-")
    )
    if escaped:
        raise AssertionError(f"temporary fetch directories escaped cleanup: {escaped}")


def write_consumer_manifest(directory: Path, org: str, name: str, version: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    content = f'''[package]\norg = "zed-fetch-canary"\nname = "consumer"\nversion = "0.0.0"\ndescription = "External resolver-only fetch canary"\n\n[package.repository]\nvcs = "git"\nurl = "https://localhost/zed-fetch-canary/consumer"\n\n[install]\ndir = "zed_modules"\n\n[dependencies]\n"{org}/{name}" = "={version}"\n'''
    (directory / ".zpkg.toml").write_text(content, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-dir", type=Path, required=True)
    parser.add_argument("--zed", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    fixture = args.fixture_dir.resolve()
    zed = args.zed.resolve()
    root = args.work_root.resolve()
    if root.exists():
        raise AssertionError(f"work root must be fresh: {root}")
    if not zed.is_file():
        raise AssertionError(f"zed binary not found: {zed}")
    if not (fixture / ".zpkg.toml").is_file():
        raise AssertionError(f"fixture has no .zpkg.toml: {fixture}")

    root.mkdir(parents=True)
    registry = root / "registry"
    registry.mkdir()
    registry_url = f"file://{registry}"
    publish_home = root / "publish-home"
    install_home = root / "install-home"
    fetch_home = root / "fetch-home-must-remain-absent"
    outputs = root / "fetch-outputs"
    outputs.mkdir()

    with (fixture / ".zpkg.toml").open("rb") as handle:
        manifest = tomllib.load(handle)
    package = manifest["package"]
    org = str(package["org"])
    name = str(package["name"])
    version = str(package["version"])

    def zed_cmd(
        *command: str | Path,
        cwd: Path,
        home: Path,
        should_fail: bool = False,
        extra_env: Mapping[str, str] | None = None,
    ) -> str:
        return run(
            [
                zed,
                "--registry",
                registry_url,
                "--home",
                home,
                *command,
            ],
            cwd=cwd,
            should_fail=should_fail,
            extra_env=extra_env,
        )

    # Strict frozen fetch requires the registry record and lock to agree on
    # real VCS provenance. The external checkout deliberately does not fetch
    # tags, so create the manifest-derived release tag locally at the exact
    # checked-out commit instead of using the publish escape hatch.
    assert_git_clean(fixture)
    release_tag = f"v{version}"
    fixture_commit = run(["git", "rev-parse", "HEAD"], cwd=fixture).strip()
    run(["git", "tag", release_tag, fixture_commit], cwd=fixture)
    zed_cmd("publish", cwd=fixture, home=publish_home)
    assert_git_clean(fixture)

    registry_metadata_path = (
        registry / "packages" / org / name / "versions" / f"{version}.json"
    )
    registry_metadata = json.loads(registry_metadata_path.read_text(encoding="utf-8"))
    if registry_metadata.get("vcs_tag") != release_tag:
        raise AssertionError("registry did not retain the exact release tag")
    if registry_metadata.get("vcs_commit") != fixture_commit:
        raise AssertionError("registry did not retain the exact source commit")

    consumer = root / "consumer"
    write_consumer_manifest(consumer, org, name, version)
    zed_cmd(
        "install",
        "--install-mode",
        "copy",
        "--adapter",
        "none",
        "--allow-ecosystem-mismatch",
        cwd=consumer,
        home=install_home,
    )
    lock = consumer / ".zpkg.lock"
    lock_bytes = lock.read_bytes()
    with lock.open("rb") as handle:
        lock_data = tomllib.load(handle)
    locked_packages = lock_data.get("package", [])
    if len(locked_packages) != 1:
        raise AssertionError(f"expected one canonical lock entry: {locked_packages!r}")
    locked = locked_packages[0]
    if locked.get("vcs_tag") != release_tag:
        raise AssertionError("lock did not preserve the registry release tag")
    if locked.get("vcs_commit") != fixture_commit:
        raise AssertionError("lock did not preserve the registry source commit")
    if not (consumer / "zed_modules" / org / name / ".zpkg.toml").is_file():
        raise AssertionError("normal install did not materialize the published fixture")

    # Start the fetch boundary from an ordinary manifest + lock, with no
    # installed package tree or global store available to the fetch command.
    zed_cmd("uninstall", cwd=consumer, home=install_home)
    if lock.read_bytes() != lock_bytes:
        raise AssertionError("uninstall changed the frozen lock input")
    shutil.rmtree(install_home)
    if (consumer / "zed_modules").exists():
        raise AssertionError("uninstall left project materialization behind")
    project_before = tree_digest(consumer)

    first = outputs / "first"
    second = outputs / "second"
    zed_cmd(
        "fetch",
        "--frozen",
        "--output",
        first,
        cwd=consumer,
        home=fetch_home,
    )
    zed_cmd(
        "fetch",
        cwd=consumer,
        home=fetch_home,
        extra_env={
            "ZED_PKG_FROZEN": "yes",
            "ZED_PKG_FETCH_OUTPUT": str(second),
        },
    )

    if tree_digest(first) != tree_digest(second):
        raise AssertionError("two frozen fetches produced different bundle bytes")
    if tree_digest(consumer) != project_before:
        raise AssertionError("resolver-only fetch mutated the consumer project")
    if fetch_home.exists():
        raise AssertionError("resolver-only fetch wrote the configured global Zed home")

    index_path = first / "metadata" / "index.json"
    index_text = index_path.read_text(encoding="utf-8")
    if str(registry) in index_text or registry_url in index_text:
        raise AssertionError("fetch metadata leaked its literal registry source")
    index = json.loads(index_text)
    if index.get("schema") != "zed.fetch/v1":
        raise AssertionError(f"unexpected fetch schema: {index.get('schema')!r}")
    packages = index.get("packages")
    if not isinstance(packages, list) or len(packages) != 1:
        raise AssertionError(f"expected one fetched package: {packages!r}")
    fetched = packages[0]
    if (fetched.get("org"), fetched.get("name"), fetched.get("version")) != (
        org,
        name,
        version,
    ):
        raise AssertionError(f"wrong package identity in fetch index: {fetched!r}")
    digest = str(fetched.get("sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise AssertionError(f"invalid artifact digest in fetch index: {digest!r}")
    if fetched.get("vcs_tag") != release_tag:
        raise AssertionError("fetch index did not preserve the release tag")
    if fetched.get("vcs_commit") != fixture_commit:
        raise AssertionError("fetch index did not preserve the source commit")
    if fetched.get("source_kind") != "file":
        raise AssertionError(f"wrong source classification: {fetched!r}")
    payload = first / "packages" / digest / "pkg"
    if not (payload / ".zpkg.toml").is_file():
        raise AssertionError(f"fetch bundle omitted verified package payload: {payload}")

    expected_lock_digest = hashlib.sha256(lock_bytes).hexdigest()
    lock_digest_text = (first / "metadata" / "lock.sha256").read_text(
        encoding="utf-8"
    )
    if lock_digest_text != f"{expected_lock_digest}  .zpkg.lock\n":
        raise AssertionError("fetch bundle retained the wrong lock digest")

    # Artifact provenance drift must fail before a final output appears.
    tampered = root / "tampered-consumer"
    shutil.copytree(consumer, tampered)
    tampered_lock = tampered / ".zpkg.lock"
    tampered_text, replacements = re.subn(
        r'^sha256 = "[0-9a-f]{64}"$',
        f'sha256 = "{"0" * 64}"',
        tampered_lock.read_text(encoding="utf-8"),
        count=1,
        flags=re.MULTILINE,
    )
    if replacements != 1:
        raise AssertionError("could not identify one lock digest to tamper")
    tampered_lock.write_text(tampered_text, encoding="utf-8")
    tampered_output = outputs / "tampered"
    failure = zed_cmd(
        "fetch",
        "--frozen",
        "--output",
        tampered_output,
        cwd=tampered,
        home=fetch_home,
        should_fail=True,
    )
    if "digest changed" not in failure:
        raise AssertionError(f"tampered fetch failed for the wrong reason:\n{failure}")
    if tampered_output.exists():
        raise AssertionError("tampered fetch published a final output")

    # Existing outputs are immutable caller-owned state.
    existing = outputs / "existing"
    existing.mkdir()
    sentinel = existing / "owned.txt"
    sentinel.write_text("caller-owned\n", encoding="utf-8")
    zed_cmd(
        "fetch",
        "--frozen",
        "--output",
        existing,
        cwd=consumer,
        home=fetch_home,
        should_fail=True,
    )
    if sentinel.read_text(encoding="utf-8") != "caller-owned\n":
        raise AssertionError("failed fetch changed a pre-existing destination")

    # Missing and dependency-free locks are both explicit, deterministic cases.
    missing = root / "missing-lock"
    missing.mkdir()
    missing_output = outputs / "missing"
    zed_cmd(
        "fetch",
        "--frozen",
        "--output",
        missing_output,
        cwd=missing,
        home=fetch_home,
        should_fail=True,
    )
    if missing_output.exists():
        raise AssertionError("missing-lock fetch published an output")

    empty = root / "empty-lock"
    empty.mkdir()
    (empty / ".zpkg.lock").write_text("version = 1\n", encoding="utf-8")
    empty_output = outputs / "empty"
    zed_cmd(
        "fetch",
        "--frozen",
        "--output",
        empty_output,
        cwd=empty,
        home=fetch_home,
    )
    empty_index = json.loads(
        (empty_output / "metadata" / "index.json").read_text(encoding="utf-8")
    )
    if empty_index.get("packages") != []:
        raise AssertionError(f"empty lock exported packages: {empty_index!r}")

    assert_no_fetch_temporaries(outputs)
    assert_git_clean(fixture)
    print(
        json.dumps(
            {
                "result": "PASS",
                "package": f"{org}/{name}@{version}",
                "vcs_tag": release_tag,
                "vcs_commit": fixture_commit,
                "artifact_sha256": digest,
                "bundle_files": len(tree_digest(first)),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
