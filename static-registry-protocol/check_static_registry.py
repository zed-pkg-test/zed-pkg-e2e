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


def safe_relative_path(relative: str) -> bool:
    if not isinstance(relative, str) or not relative or relative.startswith("/"):
        return False
    if "\\" in relative or any(
        ord(character) < 32 or ord(character) == 127 for character in relative
    ):
        return False
    return all(part not in {"", ".", ".."} for part in relative.split("/"))


def checkpoint_path_allowed(relative: str) -> bool:
    if not safe_relative_path(relative) or relative == "checkpoint.json":
        return False
    parts = relative.split("/")
    if relative == ".well-known/zpkg-registry.json":
        return True
    if len(parts) == 3 and parts[0] == "index":
        return parts[1] == parts[1].lower()
    if len(parts) == 4 and parts[0] == "pkgs" and parts[3].endswith(".tar.zst"):
        return parts[1] == parts[1].lower()
    return False


class Base:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.http = base.startswith("http://") or base.startswith("https://")
        self.root = None if self.http else Path(self.base)

    # r2.dev (and some static hosts) 403 default script User-Agents like
    # Python-urllib; real registry clients identify themselves anyway.
    UA = {"User-Agent": "zpkg-static-registry-check/0"}

    def local_path(self, rel: str) -> Path:
        if self.root is None:
            raise ValueError("HTTP base has no local path")
        if not safe_relative_path(rel):
            raise ValueError(f"unsafe static registry object path: {rel!r}")
        if self.root.is_symlink():
            raise ValueError(f"local registry root is a symbolic link: {self.root}")
        path = self.root
        for part in rel.split("/"):
            path = path / part
            if path.is_symlink():
                raise ValueError(f"local registry object path crosses a symbolic link: {rel}")
        return path

    def fetch(self, rel: str) -> bytes:
        if self.http:
            request = urllib.request.Request(f"{self.base}/{rel}", headers=self.UA)
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        return self.local_path(rel).read_bytes()

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
        return not self.local_path(rel).exists()


def semver_key(version: str):
    core = version.split("-")[0].split("+")[0]
    components = core.split(".")
    if len(components) != 3 or any(not component.isdigit() for component in components):
        raise ValueError(f"invalid v0 semantic version: {version}")
    return tuple(int(component) for component in components)


def canonical_sha256(value) -> bool:
    if not isinstance(value, str) or len(value) != 64 or value != value.lower():
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def fetch_json(base: Base, relative: str, label: str):
    try:
        return json.loads(base.fetch(relative))
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as error:
        fail(f"cannot read {label} `{relative}`: {error}")
        return None


def validated_checkpoint_entries(checkpoint: dict) -> list[dict] | None:
    if not isinstance(checkpoint, dict):
        fail("checkpoint must be a JSON object")
        return None
    if checkpoint.get("schema_version") != SCHEMA_VERSION:
        fail(
            f"checkpoint schema_version {checkpoint.get('schema_version')} != {SCHEMA_VERSION}"
        )
    if checkpoint.get("signature") is not None:
        fail("v0 checkpoint signature slot must be null")
    if not canonical_sha256(checkpoint.get("tree_sha256")):
        fail("checkpoint tree_sha256 must be canonical lowercase SHA-256")
    files = checkpoint.get("files")
    if not isinstance(files, list) or not files:
        fail("checkpoint files must be a non-empty array")
        return None

    validated = []
    paths = []
    for position, entry in enumerate(files):
        if not isinstance(entry, dict):
            fail(f"checkpoint files[{position}] must be an object")
            continue
        relative = entry.get("path")
        digest = entry.get("sha256")
        size = entry.get("size")
        if not checkpoint_path_allowed(relative):
            fail(f"checkpoint contains unsafe or unsupported object path: {relative!r}")
            continue
        if not canonical_sha256(digest):
            fail(f"checkpoint has invalid canonical SHA-256 for `{relative}`")
            continue
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            fail(f"checkpoint has invalid size for `{relative}`")
            continue
        validated.append(entry)
        paths.append(relative)

    if len(paths) != len(set(paths)):
        fail("checkpoint contains duplicate object paths")
    if paths != sorted(paths):
        fail("checkpoint object paths must be in ascending lexical order")
    if FAILURES:
        return None
    return validated


def check_local_file_set(base: Base, entries: list[dict]) -> None:
    if base.http:
        return
    root = base.root
    assert root is not None
    if not root.is_dir() or root.is_symlink():
        fail(f"local registry root must be a real directory: {root}")
        return
    expected = {entry["path"] for entry in entries}
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
    discovery = fetch_json(base, ".well-known/zpkg-registry.json", "discovery document")
    if discovery is None:
        print("== FAIL (discovery unavailable)")
        return 1
    if not isinstance(discovery, dict):
        fail("discovery document must be a JSON object")
        print(f"== FAIL ({len(FAILURES)})")
        return 1
    if discovery.get("schema_version") != SCHEMA_VERSION:
        fail(
            f"discovery schema_version {discovery.get('schema_version')} != {SCHEMA_VERSION}"
        )
    else:
        ok("schema_version matches")
    if (
        discovery.get("publish_supported") is not False
        or discovery.get("auth_modes") != ["none"]
    ):
        fail("static export must advertise publish_supported=false, auth_modes=[none]")
    else:
        ok("static-export capabilities advertised correctly")
    expected_endpoints = {
        "index": "/index",
        "pkgs": "/pkgs",
        "checkpoint": "/checkpoint.json",
    }
    if discovery.get("endpoints") != expected_endpoints:
        fail(f"discovery endpoints differ from v0 contract: {discovery.get('endpoints')}")
    else:
        ok("v0 endpoint map matches")

    print("[2] checkpoint integrity")
    checkpoint = fetch_json(base, "checkpoint.json", "checkpoint")
    if checkpoint is None:
        print(f"== FAIL ({len(FAILURES)})")
        return 1
    entries = validated_checkpoint_entries(checkpoint)
    if entries is None:
        print(f"== FAIL ({len(FAILURES)})")
        return 1

    tree_material = "".join(
        f"{entry['path']} {entry['sha256']}\n" for entry in entries
    )
    if hashlib.sha256(tree_material.encode()).hexdigest() != checkpoint.get("tree_sha256"):
        fail("tree_sha256 does not match the files list")
    else:
        ok(f"tree_sha256 consistent over {len(entries)} files")

    object_mismatches = 0
    for entry in entries:
        try:
            data = base.fetch(entry["path"])
        except (OSError, ValueError, urllib.error.URLError) as error:
            fail(f"cannot read checkpointed object `{entry['path']}`: {error}")
            object_mismatches += 1
            continue
        if (
            hashlib.sha256(data).hexdigest() != entry["sha256"]
            or len(data) != entry["size"]
        ):
            fail(f"checkpoint mismatch for {entry['path']}")
            object_mismatches += 1
    if object_mismatches == 0:
        ok("every checkpointed object matches its recorded sha256+size")
    check_local_file_set(base, entries)

    print("[3] indexes: NDJSON, semver order, tarball checksums")
    index_paths = [
        entry["path"] for entry in entries if entry["path"].startswith("index/")
    ]
    if not index_paths:
        fail("no index files listed in checkpoint")
    resolvable: dict[str, list[str]] = {}
    parsed_indexes: dict[str, list[dict]] = {}
    for index_path in index_paths:
        _, org, name = index_path.split("/", 2)
        try:
            raw_index = base.fetch(index_path).decode()
            lines = [json.loads(line) for line in raw_index.splitlines() if line.strip()]
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError, urllib.error.URLError) as error:
            fail(f"cannot parse index `{index_path}`: {error}")
            continue
        if not lines or any(not isinstance(line, dict) for line in lines):
            fail(f"{index_path}: every NDJSON line must be an object")
            continue
        versions = [line.get("version") for line in lines]
        try:
            ordered = sorted(versions, key=semver_key)
        except (AttributeError, TypeError, ValueError) as error:
            fail(f"{index_path}: invalid semantic version: {error}")
            continue
        if versions != ordered or len(set(versions)) != len(versions):
            fail(f"{index_path}: versions not unique/ascending semver")
        for line in lines:
            version = line.get("version")
            checksum = line.get("cksum")
            size = line.get("size")
            yanked = line.get("yanked")
            if not isinstance(checksum, str) or ":" not in checksum:
                fail(f"{index_path}: invalid checksum for version {version!r}")
                continue
            algo, digest = checksum.split(":", 1)
            if algo != "sha256" or not canonical_sha256(digest):
                fail(f"{index_path}: unsupported or malformed checksum `{checksum}`")
                continue
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                fail(f"{index_path}: invalid size for version {version!r}")
                continue
            if not isinstance(yanked, bool):
                fail(f"{index_path}: yanked must be boolean for version {version!r}")
                continue
            artifact_path = f"pkgs/{org}/{name}/{version}.tar.zst"
            try:
                blob = base.fetch(artifact_path)
            except (OSError, ValueError, urllib.error.URLError) as error:
                fail(f"cannot read artifact `{artifact_path}`: {error}")
                continue
            if hashlib.sha256(blob).hexdigest() != digest or len(blob) != size:
                fail(f"{org}/{name}@{version}: tarball does not match index cksum/size")
        parsed_indexes[index_path] = lines
        resolvable[f"{org}/{name}"] = [
            line["version"] for line in lines if line.get("yanked") is False
        ]
        ok(f"{org}/{name}: {len(lines)} versions verified")

    print("[4] yank semantics")
    yanked_seen = False
    for index_path, lines in parsed_indexes.items():
        _, org, name = index_path.split("/", 2)
        for line in lines:
            if line.get("yanked") is True:
                yanked_seen = True
                if line.get("version") in resolvable.get(f"{org}/{name}", []):
                    fail(f"{org}/{name}@{line.get('version')} yanked but resolvable")
                artifact_path = f"pkgs/{org}/{name}/{line.get('version')}.tar.zst"
                try:
                    base.fetch(artifact_path)
                except (OSError, ValueError, urllib.error.URLError) as error:
                    fail(f"yanked package bytes are not retained at `{artifact_path}`: {error}")
                else:
                    ok(
                        f"{org}/{name}@{line.get('version')}: "
                        "yanked => unresolvable, bytes retained"
                    )
    if not yanked_seen:
        fail("fixture set contains no yanked version — yank path untested (vacuous)")

    print("[5] absent package => missing/404")
    try:
        absent = base.missing("index/zpkg-e2e/definitely-not-a-package")
    except (OSError, ValueError, urllib.error.URLError) as error:
        fail(f"cannot test absent package: {error}")
    else:
        if absent:
            ok("absent index is a hard miss")
        else:
            fail("absent package index unexpectedly present")

    print(f"== {'PASS' if not FAILURES else f'FAIL ({len(FAILURES)})'}")
    return 1 if FAILURES else 0


def rewrite_checkpoint(tree: Path, transform) -> None:
    checkpoint_path = tree / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text())
    transform(checkpoint)
    files = checkpoint.get("files", [])
    tree_material = "".join(
        f"{entry['path']} {entry['sha256']}\n" for entry in files
    )
    checkpoint["tree_sha256"] = hashlib.sha256(tree_material.encode()).hexdigest()
    checkpoint_path.write_text(json.dumps(checkpoint, indent=2, sort_keys=True) + "\n")


MUTATIONS = {
    "tamper-tarball": lambda tree: _flip_byte(next((tree / "pkgs").rglob("*.tar.zst"))),
    "tamper-index": lambda tree: _append(
        next(path for path in sorted((tree / "index").rglob("*")) if path.is_file()),
        b'{"version":"9.9.9","deps":[],"cksum":"sha256:00","size":2,"yanked":false}\n',
    ),
    "drop-checkpoint-entry": lambda tree: rewrite_checkpoint(
        tree, lambda checkpoint: checkpoint["files"].pop(0)
    ),
    "resurrect-yanked": lambda tree: _unyank(tree),
    "wrong-schema": lambda tree: _bump_schema(tree),
    "uncheckpointed-object": lambda tree: _add_uncheckpointed_object(tree),
    "remove-checkpointed-object": lambda tree: _remove_checkpointed_object(tree),
    "unsafe-checkpoint-path": lambda tree: _unsafe_checkpoint_path(tree),
    "duplicate-checkpoint-path": lambda tree: _duplicate_checkpoint_path(tree),
}


def _flip_byte(path: Path) -> None:
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0xFF
    path.write_bytes(bytes(data))


def _append(path: Path, extra: bytes) -> None:
    path.write_bytes(path.read_bytes() + extra)


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


def _remove_checkpointed_object(tree: Path) -> None:
    checkpoint = json.loads((tree / "checkpoint.json").read_text())
    relative = checkpoint["files"][0]["path"]
    (tree / relative).unlink()


def _unsafe_checkpoint_path(tree: Path) -> None:
    rewrite_checkpoint(
        tree,
        lambda checkpoint: checkpoint["files"][0].update({"path": "../outside-secret"}),
    )


def _duplicate_checkpoint_path(tree: Path) -> None:
    rewrite_checkpoint(
        tree,
        lambda checkpoint: checkpoint["files"].append(dict(checkpoint["files"][0])),
    )


def self_test(tree: Path) -> int:
    print("== red tests: every mutation must make the checker fail")
    if not MUTATIONS:
        print("== self-test FAIL: no mutations declared")
        return 1
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
    print(f"== self-test {'PASS' if bad == 0 else 'FAIL'} ({len(MUTATIONS)} mutations)")
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
