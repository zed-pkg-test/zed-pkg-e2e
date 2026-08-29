#!/usr/bin/env python3
"""Real-CLI certification for DEN-3017 publish-ignore semantics."""

from __future__ import annotations

import argparse
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Iterable

WARNING_PREFIX = "warning: both [publish].exclude and .zedignore define publish rules"


def manifest(name: str, publish_rules: Iterable[str] = (), extra: str = "") -> str:
    rules = list(publish_rules)
    publish = ""
    if rules:
        encoded = ", ".join(f'"{rule}"' for rule in rules)
        publish = f"\n[publish]\nexclude = [{encoded}]\n"
    return f'''[package]
org = "den3017"
name = "{name}"
version = "1.0.0"

[package.repository]
url = "https://example.invalid/den3017/{name}"
{publish}{extra}
'''


def write_package(root: Path, name: str, rules: Iterable[str] = (), extra: str = "") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".zpkg.toml").write_text(manifest(name, rules, extra), encoding="utf-8")
    (root / "LICENSE").write_text("MIT\n", encoding="utf-8")


def pack(zed: Path, root: Path, *, success: bool = True) -> tuple[subprocess.CompletedProcess[str], set[str]]:
    out = root / "artifacts"
    out.mkdir(exist_ok=True)
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    proc = subprocess.run(
        [str(zed), "pack", "--out", str(out)],
        cwd=root,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if success and proc.returncode != 0:
        raise AssertionError(
            f"zed pack failed in {root.name}:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    if not success:
        if proc.returncode == 0:
            raise AssertionError(f"zed pack unexpectedly succeeded in {root.name}")
        return proc, set()

    archives = sorted(out.glob("*.tar.gz"))
    if len(archives) != 1:
        raise AssertionError(f"expected one tar.gz in {out}, found {archives}")
    with tarfile.open(archives[0], "r:gz") as archive:
        entries = {member.name.replace("\\", "/") for member in archive.getmembers()}
    return proc, entries


def pack_polyglot(zed: Path, root: Path) -> tuple[subprocess.CompletedProcess[str], list[set[str]]]:
    out = root / "artifacts"
    out.mkdir(exist_ok=True)
    proc = subprocess.run(
        [str(zed), "pack", "--out", str(out)],
        cwd=root,
        env={**os.environ, "NO_COLOR": "1"},
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"polyglot pack failed:\n{proc.stdout}\n{proc.stderr}")
    archives = sorted(out.glob("*.tar.gz"))
    if len(archives) != 2:
        raise AssertionError(f"expected two polyglot archives, found {archives}")
    entries: list[set[str]] = []
    for path in archives:
        with tarfile.open(path, "r:gz") as archive:
            entries.append({member.name.replace("\\", "/") for member in archive.getmembers()})
    return proc, entries


def warning_count(proc: subprocess.CompletedProcess[str]) -> int:
    return proc.stderr.count(WARNING_PREFIX)


def assert_present(entries: set[str], *paths: str) -> None:
    missing = [path for path in paths if path not in entries]
    if missing:
        raise AssertionError(f"archive is missing {missing}; entries={sorted(entries)}")


def assert_absent(entries: set[str], *paths: str) -> None:
    leaked = [path for path in paths if path in entries]
    if leaked:
        raise AssertionError(f"archive leaked {leaked}; entries={sorted(entries)}")


def scenario_ignore_only(zed: Path, base: Path) -> None:
    root = base / "ignore-only"
    write_package(root, "ignore-only")
    (root / "keep.txt").write_text("keep", encoding="utf-8")
    (root / ".env.local").write_text("secret", encoding="utf-8")
    (root / ".hidden").mkdir()
    (root / ".hidden" / "cache.bin").write_text("cache", encoding="utf-8")
    (root / ".zedignore").write_text(".env*\n.hidden/**\n", encoding="utf-8")
    proc, entries = pack(zed, root)
    assert warning_count(proc) == 0, proc.stderr
    assert_present(entries, "pkg/keep.txt", "pkg/.zpkg.toml", "pkg/LICENSE")
    assert_absent(entries, "pkg/.env.local", "pkg/.hidden/cache.bin", "pkg/.zedignore")


def scenario_manifest_only(zed: Path, base: Path) -> None:
    root = base / "manifest-only"
    write_package(root, "manifest-only", ["manifest-only.txt"])
    (root / "keep.txt").write_text("keep", encoding="utf-8")
    (root / "manifest-only.txt").write_text("drop", encoding="utf-8")
    proc, entries = pack(zed, root)
    assert warning_count(proc) == 0, proc.stderr
    assert_present(entries, "pkg/keep.txt")
    assert_absent(entries, "pkg/manifest-only.txt")


def scenario_union(zed: Path, base: Path) -> None:
    root = base / "union"
    write_package(root, "union", ["manifest-only.txt"])
    for name in ["keep.txt", "manifest-only.txt", "ignore-only.txt"]:
        (root / name).write_text(name, encoding="utf-8")
    (root / ".zedignore").write_text("# local output\n\nignore-only.txt\n", encoding="utf-8")
    proc, entries = pack(zed, root)
    assert warning_count(proc) == 1, proc.stderr
    assert "contradictory paths" not in proc.stderr, proc.stderr
    assert_present(entries, "pkg/keep.txt")
    assert_absent(entries, "pkg/manifest-only.txt", "pkg/ignore-only.txt", "pkg/.zedignore")


def scenario_sorted_conflict(zed: Path, base: Path) -> None:
    root = base / "sorted-conflict"
    write_package(root, "sorted-conflict", ["dist/**", "cache/**"])
    for directory in ["dist", "cache"]:
        (root / directory).mkdir()
        (root / directory / "value.txt").write_text(directory, encoding="utf-8")
    (root / ".zedignore").write_text("!dist\n!cache\n", encoding="utf-8")
    proc, entries = pack(zed, root)
    assert warning_count(proc) == 1, proc.stderr
    assert "contradictory paths kept excluded: cache, dist" in proc.stderr, proc.stderr
    assert_absent(entries, "pkg/dist/value.txt", "pkg/cache/value.txt")


def scenario_reverse_conflict(zed: Path, base: Path) -> None:
    root = base / "reverse-conflict"
    write_package(root, "reverse-conflict", ["!secret"])
    (root / "secret").mkdir()
    (root / "secret" / "value.txt").write_text("secret", encoding="utf-8")
    (root / ".zedignore").write_text("secret/**\n", encoding="utf-8")
    proc, entries = pack(zed, root)
    assert warning_count(proc) == 1, proc.stderr
    assert "contradictory paths kept excluded: secret" in proc.stderr, proc.stderr
    assert_absent(entries, "pkg/secret/value.txt")


def scenario_source_local_negation(zed: Path, base: Path) -> None:
    manifest_root = base / "manifest-negation"
    write_package(manifest_root, "manifest-negation", ["scratch/**", "!scratch"])
    (manifest_root / "scratch").mkdir()
    (manifest_root / "scratch" / "value.txt").write_text("kept", encoding="utf-8")
    proc, entries = pack(zed, manifest_root)
    assert warning_count(proc) == 0, proc.stderr
    assert_present(entries, "pkg/scratch/value.txt")

    ignore_root = base / "ignore-negation"
    write_package(ignore_root, "ignore-negation")
    (ignore_root / "scratch").mkdir()
    (ignore_root / "scratch" / "value.txt").write_text("kept", encoding="utf-8")
    (ignore_root / ".zedignore").write_text("scratch/**\n!scratch\n", encoding="utf-8")
    proc, entries = pack(zed, ignore_root)
    assert warning_count(proc) == 0, proc.stderr
    assert_present(entries, "pkg/scratch/value.txt")
    assert_absent(entries, "pkg/.zedignore")


def scenario_invalid_glob(zed: Path, base: Path) -> None:
    root = base / "invalid-glob"
    write_package(root, "invalid-glob")
    (root / "keep.txt").write_text("keep", encoding="utf-8")
    (root / ".zedignore").write_text("[broken\n", encoding="utf-8")
    proc, _ = pack(zed, root, success=False)
    if "invalid glob pattern" not in proc.stderr:
        raise AssertionError(f"invalid glob error was not actionable:\n{proc.stderr}")


def scenario_polyglot(zed: Path, base: Path) -> None:
    root = base / "polyglot"
    (root / "clients" / "ts" / "src").mkdir(parents=True)
    (root / "clients" / "java" / "src").mkdir(parents=True)
    (root / "LICENSE").write_text("MIT\n", encoding="utf-8")
    (root / "clients" / "ts" / "package.json").write_text('{"name":"@den3017/client"}', encoding="utf-8")
    (root / "clients" / "ts" / "src" / "index.js").write_text("export {};", encoding="utf-8")
    (root / "clients" / "ts" / "private.key").write_text("private", encoding="utf-8")
    (root / "clients" / "ts" / ".zedignore").write_text("private.key\n", encoding="utf-8")
    (root / "clients" / "java" / "pom.xml").write_text("<project></project>", encoding="utf-8")
    (root / "clients" / "java" / "src" / "Client.java").write_text("class Client {}", encoding="utf-8")
    (root / ".zpkg.toml").write_text(
        manifest(
            "polyglot",
            extra='''
[targets.nodejs]
dir = "clients/ts"
adapter = "node"

[targets.java]
dir = "clients/java"
adapter = "java"
''',
        ),
        encoding="utf-8",
    )
    proc, archives = pack_polyglot(zed, root)
    assert warning_count(proc) == 0, proc.stderr
    node = next(entries for entries in archives if "pkg/package.json" in entries)
    java = next(entries for entries in archives if "pkg/pom.xml" in entries)
    assert_present(node, "pkg/package.json", "pkg/src/index.js", "pkg/LICENSE")
    assert_absent(node, "pkg/private.key", "pkg/.zedignore", "pkg/pom.xml")
    assert_present(java, "pkg/pom.xml", "pkg/src/Client.java", "pkg/LICENSE")
    assert_absent(java, "pkg/package.json")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zed", required=True, type=Path)
    args = parser.parse_args()
    zed = args.zed.resolve()
    if not zed.is_file():
        raise SystemExit(f"zed binary not found: {zed}")

    with tempfile.TemporaryDirectory(prefix="den-3017-") as temp:
        base = Path(temp)
        scenarios = [
            scenario_ignore_only,
            scenario_manifest_only,
            scenario_union,
            scenario_sorted_conflict,
            scenario_reverse_conflict,
            scenario_source_local_negation,
            scenario_invalid_glob,
            scenario_polyglot,
        ]
        for scenario in scenarios:
            scenario(zed, base)
            print(f"ok: {scenario.__name__}")
    print("DEN-3017 real-CLI certification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
