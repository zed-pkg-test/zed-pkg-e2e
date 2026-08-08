#!/usr/bin/env python3
"""Build a deterministic static zpkg registry tree (protocol sketch v0).

Groundwork fixture for the registry protocol RFC (Linear DEN-2854) and the
static-export / e2e phases (DEN-2857, DEN-2861). The signed-checkpoint design
is owned by the RFC; this v0 emits an UNSIGNED checkpoint structured so a
signature slots in without changing the tree layout.

Layout produced under --out:
  .well-known/zpkg-registry.json      discovery document
  index/<org>/<name>                  NDJSON, one line per version (semver order)
  pkgs/<org>/<name>/<version>.tar.zst content-addressed deterministic tarballs
  checkpoint.json                     {schema_version, seq, generated_at,
                                       files:[{path,sha256,size}], tree_sha256}

Determinism: tar entries sorted, mtime/uid/gid zeroed via SOURCE_DATE_EPOCH
(default 0), zstd -19 with a pinned-version note recorded in the checkpoint.
Same inputs => byte-identical tree.

Safety: output must be a new or empty real directory. Fixture symlinks and
special files are rejected rather than followed into the package archive. The
complete tree is written to a same-parent staging directory and renamed into
place only after all fixture validation and file writes succeed.
"""
import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

SCHEMA_VERSION = 0
EPOCH = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_output_target(out: Path) -> tuple[Path, bool]:
    if out.is_symlink():
        raise ValueError(f"output path is a symbolic link: {out}")
    existed_empty = False
    if out.exists():
        if not out.is_dir():
            raise ValueError(f"output path is not a directory: {out}")
        if any(out.iterdir()):
            raise ValueError(
                f"output directory must be empty to prevent stale registry objects: {out}"
            )
        existed_empty = True

    parent = out.parent if out.parent != Path("") else Path(".")
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError(f"output parent must be a real directory: {parent}")
    return parent, existed_empty


def publish_tree(out: Path, files: dict[str, bytes]) -> None:
    parent, existed_empty = validate_output_target(out)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{out.name}.zpkg-static-",
            dir=parent,
        )
    )
    committed = False
    removed_empty_output = False
    try:
        for relative, data in sorted(files.items()):
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        if existed_empty:
            out.rmdir()
            removed_empty_output = True
        os.replace(staging, out)
        committed = True
    finally:
        if not committed and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if not committed and removed_empty_output and not out.exists():
            out.mkdir()


def deterministic_tar_zst(pkg_dir: Path) -> bytes:
    buffer = io.BytesIO()
    paths = sorted(
        pkg_dir.rglob("*"),
        key=lambda path: path.relative_to(pkg_dir).as_posix(),
    )
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for path in paths:
            relative = path.relative_to(pkg_dir)
            if path.is_symlink():
                raise ValueError(
                    "fixture contains a symbolic link, which static export will not follow: "
                    f"{relative}"
                )
            if path.is_dir():
                continue
            if not path.is_file():
                raise ValueError(f"fixture contains an unsupported special file: {relative}")
            data = path.read_bytes()
            entry = tarfile.TarInfo(name=relative.as_posix())
            entry.size = len(data)
            entry.mtime = EPOCH
            entry.uid = entry.gid = 0
            entry.uname = entry.gname = ""
            entry.mode = 0o644
            archive.addfile(entry, io.BytesIO(data))
    result = subprocess.run(
        ["zstd", "-19", "-q", "--no-progress", "-c"],
        input=buffer.getvalue(),
        stdout=subprocess.PIPE,
        check=True,
    )
    return result.stdout


def semver_key(version: str):
    core = version.split("-")[0].split("+")[0]
    return tuple(int(component) for component in core.split("."))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--seq", type=int, default=1, help="checkpoint sequence number")
    args = parser.parse_args()

    if not args.fixtures.is_dir() or args.fixtures.is_symlink():
        print(f"ERROR: fixtures path must be a real directory: {args.fixtures}", file=sys.stderr)
        return 2

    # Validate the caller-owned destination before spending compression work,
    # but do not create or mutate it until the complete tree is ready.
    try:
        validate_output_target(args.out)
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    objects: dict[str, bytes] = {}

    def emit(relative: str, data: bytes) -> None:
        if relative in objects:
            raise ValueError(f"duplicate static registry object path: {relative}")
        objects[relative] = data

    # Packages: fixtures/<org>/<name>/<version>/{zpkg.json, files...}
    packages: dict[tuple[str, str], list[dict]] = {}
    for meta_path in sorted(args.fixtures.glob("*/*/*/zpkg.json")):
        version_dir = meta_path.parent
        version = version_dir.name
        name = version_dir.parent.name
        org = version_dir.parent.parent.name
        if org != org.lower():
            print(f"ERROR: org segment must be lowercase canonical form: {org}", file=sys.stderr)
            return 2
        try:
            metadata = json.loads(meta_path.read_text())
            blob = deterministic_tar_zst(version_dir)
        except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
            print(f"ERROR: cannot export {org}/{name}@{version}: {error}", file=sys.stderr)
            return 2
        emit(f"pkgs/{org}/{name}/{version}.tar.zst", blob)
        packages.setdefault((org, name), []).append(
            {
                "version": version,
                "deps": metadata.get("deps", []),
                "cksum": f"sha256:{sha256_bytes(blob)}",
                "size": len(blob),
                "yanked": bool(metadata.get("yanked", False)),
            }
        )

    if not packages:
        print("ERROR: fixture set contains no package versions", file=sys.stderr)
        return 2

    try:
        for (org, name), lines in sorted(packages.items()):
            lines.sort(key=lambda line: semver_key(line["version"]))
            ndjson = "".join(
                json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n"
                for line in lines
            )
            emit(f"index/{org}/{name}", ndjson.encode())
    except (ValueError, TypeError) as error:
        print(f"ERROR: invalid package version metadata: {error}", file=sys.stderr)
        return 2

    discovery = {
        "schema_version": SCHEMA_VERSION,
        "registry_kind": "static-export",
        "endpoints": {
            "index": "/index",
            "pkgs": "/pkgs",
            "checkpoint": "/checkpoint.json",
        },
        "auth_modes": ["none"],
        "publish_supported": False,
        "notes": "protocol sketch v0 fixture; signed checkpoint + registry_id land with the DEN-2854 RFC",
    }
    emit(
        ".well-known/zpkg-registry.json",
        (json.dumps(discovery, indent=2, sort_keys=True) + "\n").encode(),
    )

    files_entry = [
        {"path": relative, "sha256": sha256_bytes(data), "size": len(data)}
        for relative, data in sorted(objects.items())
    ]
    tree_material = "".join(
        f"{entry['path']} {entry['sha256']}\n" for entry in files_entry
    )
    checkpoint = {
        "schema_version": SCHEMA_VERSION,
        "seq": args.seq,
        "generated_at_epoch": EPOCH,
        "zstd_note": "compressed with zstd -19; version recorded by the sync evidence, not trusted by verifiers",
        "signature": None,
        "files": files_entry,
        "tree_sha256": sha256_bytes(tree_material.encode()),
    }
    emit(
        "checkpoint.json",
        (json.dumps(checkpoint, indent=2, sort_keys=True) + "\n").encode(),
    )

    try:
        publish_tree(args.out, objects)
    except (OSError, ValueError) as error:
        print(f"ERROR: cannot publish static registry tree: {error}", file=sys.stderr)
        return 2

    print(
        f"built {len(files_entry)} objects + checkpoint "
        f"(tree_sha256={checkpoint['tree_sha256'][:12]}…)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
