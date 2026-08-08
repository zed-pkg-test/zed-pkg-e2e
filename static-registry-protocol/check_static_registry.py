#!/usr/bin/env python3
"""Conformance checker for the static zpkg registry tree (protocol sketch v0).

Runs against a local tree (--base /path) or a live static host
(--base https://host). Also provides `self-test`, which proves every
assertion can fail (mutation/red tests per the DEN-2861 acceptance rule:
a green check is meaningless unless red was reachable).
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

SCHEMA_VERSION = 0
FAILURES: list[str] = []


def fail(msg: str) -> None:
    FAILURES.append(msg)
    print(f"  FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"  ok: {msg}")


class Base:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.http = base.startswith("http://") or base.startswith("https://")

    # r2.dev (and some static hosts) 403 default script User-Agents like
    # Python-urllib; real registry clients identify themselves anyway.
    UA = {"User-Agent": "zpkg-static-registry-check/0"}

    def fetch(self, rel: str) -> bytes:
        if self.http:
            req = urllib.request.Request(f"{self.base}/{rel}", headers=self.UA)
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.read()
        return (Path(self.base) / rel).read_bytes()

    def missing(self, rel: str) -> bool:
        if self.http:
            try:
                urllib.request.urlopen(
                    urllib.request.Request(f"{self.base}/{rel}", headers=self.UA),
                    timeout=15,
                )
                return False
            except urllib.error.HTTPError as error:
                return error.code == 404
        return not (Path(self.base) / rel).exists()


def semver_key(v: str):
    core = v.split("-")[0].split("+")[0]
    return tuple(int(x) for x in core.split("."))


def check_local_file_set(base: Base, checkpoint: dict) -> None:
    if base.http:
        return
    root = Path(base.base)
    expected = {entry["path"] for entry in checkpoint["files"]}
    expected.add("checkpoint.json")
    actual: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            fail(f"local registry tree contains symbolic link `{relative}`")
        elif path.is_file():
            actual.add(relative)
        elif not path.is_dir():
            fail(f"local registry tree contains special file `{relative}`")
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        fail(f"local registry tree is missing checkpointed files: {missing}")
    if extra:
        fail(f"local registry tree contains uncheckpointed files: {extra}")
    if not missing and not extra:
        ok("local file set exactly matches checkpoint plus checkpoint.json")


def run_checks(base: Base) -> int:
    FAILURES.clear()
    print(f"== conformance against {base.base}")

    print("[1] discovery document")
    disc = json.loads(base.fetch(".well-known/zpkg-registry.json"))
    if disc.get("schema_version") != SCHEMA_VERSION:
        fail(f"discovery schema_version {disc.get('schema_version')} != {SCHEMA_VERSION}")
    else:
        ok("schema_version matches")
    if disc.get("publish_supported") is not False or disc.get("auth_modes") != ["none"]:
        fail("static export must advertise publish_supported=false, auth_modes=[none]")
    else:
        ok("static-export capabilities advertised correctly")

    print("[2] checkpoint integrity")
    cp = json.loads(base.fetch("checkpoint.json"))
    tree_material = "".join(f"{entry['path']} {entry['sha256']}\n" for entry in cp["files"])
    if hashlib.sha256(tree_material.encode()).hexdigest() != cp["tree_sha256"]:
        fail("tree_sha256 does not match the files list")
    else:
        ok(f"tree_sha256 consistent over {len(cp['files'])} files")
    for entry in cp["files"]:
        data = base.fetch(entry["path"])
        if hashlib.sha256(data).hexdigest() != entry["sha256"] or len(data) != entry["size"]:
            fail(f"checkpoint mismatch for {entry['path']}")
    if not any("checkpoint mismatch" in failure for failure in FAILURES):
        ok("every checkpointed object matches its recorded sha256+size")
    check_local_file_set(base, cp)

    print("[3] indexes: NDJSON, semver order, tarball checksums")
    index_paths = [
        entry["path"] for entry in cp["files"] if entry["path"].startswith("index/")
    ]
    if not index_paths:
        fail("no index files listed in checkpoint")
    resolvable: dict[str, list[str]] = {}
    for index_path in index_paths:
        _, org, name = index_path.split("/", 2)
        lines = [
            json.loads(line)
            for line in base.fetch(index_path).decode().splitlines()
            if line.strip()
        ]
        versions = [line["version"] for line in lines]
        if versions != sorted(versions, key=semver_key) or len(set(versions)) != len(versions):
            fail(f"{index_path}: versions not unique/ascending semver")
        for line in lines:
            algo, digest = line["cksum"].split(":", 1)
            if algo != "sha256":
                fail(f"{index_path}: unsupported cksum algo {algo}")
            blob = base.fetch(f"pkgs/{org}/{name}/{line['version']}.tar.zst")
            if hashlib.sha256(blob).hexdigest() != digest or len(blob) != line["size"]:
                fail(
                    f"{org}/{name}@{line['version']}: tarball does not match index cksum/size"
                )
        resolvable[f"{org}/{name}"] = [
            line["version"] for line in lines if not line["yanked"]
        ]
        ok(f"{org}/{name}: {len(lines)} versions verified")

    print("[4] yank semantics")
    yanked_seen = False
    for index_path in index_paths:
        _, org, name = index_path.split("/", 2)
        for line in (
            json.loads(raw)
            for raw in base.fetch(index_path).decode().splitlines()
            if raw.strip()
        ):
            if line["yanked"]:
                yanked_seen = True
                if line["version"] in resolvable[f"{org}/{name}"]:
                    fail(f"{org}/{name}@{line['version']} yanked but resolvable")
                base.fetch(f"pkgs/{org}/{name}/{line['version']}.tar.zst")
                ok(
                    f"{org}/{name}@{line['version']}: "
                    "yanked => unresolvable, bytes retained"
                )
    if not yanked_seen:
        fail("fixture set contains no yanked version — yank path untested (vacuous)")

    print("[5] absent package => missing/404")
    if base.missing("index/zpkg-e2e/definitely-not-a-package"):
        ok("absent index is a hard miss")
    else:
        fail("absent package index unexpectedly present")

    print(f"== {'PASS' if not FAILURES else f'FAIL ({len(FAILURES)})'}")
    return 1 if FAILURES else 0


MUTATIONS = {
    "tamper-tarball": lambda tree: _flip_byte(next((tree / "pkgs").rglob("*.tar.zst"))),
    "tamper-index": lambda tree: _append(
        next(path for path in sorted((tree / "index").rglob("*")) if path.is_file()),
        b'{"version":"9.9.9","deps":[],"cksum":"sha256:00","size":2,"yanked":false}\n',
    ),
    "drop-checkpoint-entry": lambda tree: _drop_cp_entry(tree),
    "resurrect-yanked": lambda tree: _unyank(tree),
    "wrong-schema": lambda tree: _bump_schema(tree),
    "uncheckpointed-object": lambda tree: _add_uncheckpointed_object(tree),
}


def _flip_byte(path: Path) -> None:
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0xFF
    path.write_bytes(bytes(data))


def _append(path: Path, extra: bytes) -> None:
    path.write_bytes(path.read_bytes() + extra)


def _drop_cp_entry(tree: Path) -> None:
    checkpoint = json.loads((tree / "checkpoint.json").read_text())
    checkpoint["files"] = checkpoint["files"][1:]
    (tree / "checkpoint.json").write_text(
        json.dumps(checkpoint, indent=2, sort_keys=True) + "\n"
    )


def _unyank(tree: Path) -> None:
    for index_path in (tree / "index").rglob("*"):
        if index_path.is_file():
            lines = [
                json.loads(line)
                for line in index_path.read_text().splitlines()
                if line.strip()
            ]
            changed = False
            for line in lines:
                if line["yanked"]:
                    line["yanked"] = False
                    changed = True
            if changed:
                index_path.write_text(
                    "".join(
                        json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n"
                        for line in lines
                    )
                )
                return
    raise SystemExit("no yanked line to mutate")


def _bump_schema(tree: Path) -> None:
    discovery_path = tree / ".well-known/zpkg-registry.json"
    document = json.loads(discovery_path.read_text())
    document["schema_version"] = 99
    discovery_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")


def _add_uncheckpointed_object(tree: Path) -> None:
    path = tree / "index/zpkg-e2e/uncheckpointed-package"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"version":"1.0.0","deps":[],"cksum":"sha256:00","size":0,"yanked":false}\n'
    )


def self_test(tree: Path) -> int:
    print("== red tests: every mutation must make the checker fail")
    bad = 0
    for name, mutate in MUTATIONS.items():
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory) / "tree"
            shutil.copytree(tree, copy)
            mutate(copy)
            result = subprocess.run(
                [sys.executable, __file__, "--base", str(copy)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            verdict = (
                "red as required"
                if result.returncode != 0
                else "STAYED GREEN — checker is vacuous here"
            )
            print(f"  {name}: {verdict}")
            if result.returncode == 0:
                bad += 1
    print(f"== self-test {'PASS' if bad == 0 else 'FAIL'}")
    return 1 if bad else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", help="tree path or https base URL")
    parser.add_argument(
        "cmd",
        nargs="?",
        choices=["self-test"],
        help="self-test runs the mutation red tests against --base (local tree)",
    )
    args = parser.parse_args()
    if not args.base:
        parser.error("--base required")
    if args.cmd == "self-test":
        return self_test(Path(args.base))
    return run_checks(Base(args.base))


if __name__ == "__main__":
    raise SystemExit(main())
