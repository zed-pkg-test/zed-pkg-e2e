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
Same inputs => byte-identical tree (verified by check_static_registry.py
--rebuild-compare).
"""
import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

SCHEMA_VERSION = 0
EPOCH = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def deterministic_tar_zst(pkg_dir: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for p in sorted(pkg_dir.rglob("*")):
            if not p.is_file():
                continue
            ti = tarfile.TarInfo(name=str(p.relative_to(pkg_dir)))
            data = p.read_bytes()
            ti.size = len(data)
            ti.mtime = EPOCH
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            ti.mode = 0o644
            tf.addfile(ti, io.BytesIO(data))
    proc = subprocess.run(
        ["zstd", "-19", "-q", "--no-progress", "-c"],
        input=buf.getvalue(),
        stdout=subprocess.PIPE,
        check=True,
    )
    return proc.stdout


def semver_key(v: str):
    core = v.split("-")[0].split("+")[0]
    return tuple(int(x) for x in core.split("."))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--seq", type=int, default=1, help="checkpoint sequence number")
    args = ap.parse_args()

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    written: list[tuple[str, bytes]] = []

    def emit(rel: str, data: bytes) -> None:
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        written.append((rel, data))

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
        meta = json.loads(meta_path.read_text())
        blob = deterministic_tar_zst(version_dir)
        emit(f"pkgs/{org}/{name}/{version}.tar.zst", blob)
        packages.setdefault((org, name), []).append(
            {
                "version": version,
                "deps": meta.get("deps", []),
                "cksum": f"sha256:{sha256_bytes(blob)}",
                "size": len(blob),
                "yanked": bool(meta.get("yanked", False)),
            }
        )

    for (org, name), lines in sorted(packages.items()):
        lines.sort(key=lambda l: semver_key(l["version"]))
        ndjson = "".join(json.dumps(l, sort_keys=True, separators=(",", ":")) + "\n" for l in lines)
        emit(f"index/{org}/{name}", ndjson.encode())

    discovery = {
        "schema_version": SCHEMA_VERSION,
        "registry_kind": "static-export",
        "endpoints": {"index": "/index", "pkgs": "/pkgs", "checkpoint": "/checkpoint.json"},
        "auth_modes": ["none"],
        "publish_supported": False,
        "notes": "protocol sketch v0 fixture; signed checkpoint + registry_id land with the DEN-2854 RFC",
    }
    emit(".well-known/zpkg-registry.json", (json.dumps(discovery, indent=2, sort_keys=True) + "\n").encode())

    files_entry = [
        {"path": rel, "sha256": sha256_bytes(data), "size": len(data)}
        for rel, data in sorted(written)
    ]
    tree_material = "".join(f"{f['path']} {f['sha256']}\n" for f in files_entry)
    checkpoint = {
        "schema_version": SCHEMA_VERSION,
        "seq": args.seq,
        "generated_at_epoch": EPOCH,
        "zstd_note": "compressed with zstd -19; version recorded by the sync evidence, not trusted by verifiers",
        "signature": None,
        "files": files_entry,
        "tree_sha256": sha256_bytes(tree_material.encode()),
    }
    (out / "checkpoint.json").write_bytes(
        (json.dumps(checkpoint, indent=2, sort_keys=True) + "\n").encode()
    )
    print(f"built {len(files_entry)} objects + checkpoint (tree_sha256={checkpoint['tree_sha256'][:12]}…)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
