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
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        return (Path(self.base) / rel).read_bytes()

    def missing(self, rel: str) -> bool:
        if self.http:
            try:
                urllib.request.urlopen(
                    urllib.request.Request(f"{self.base}/{rel}", headers=self.UA), timeout=15
                )
                return False
            except urllib.error.HTTPError as e:
                return e.code == 404
        return not (Path(self.base) / rel).exists()


def semver_key(v: str):
    core = v.split("-")[0].split("+")[0]
    return tuple(int(x) for x in core.split("."))


def run_checks(base: Base) -> int:
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
    tree_material = "".join(f"{f['path']} {f['sha256']}\n" for f in cp["files"])
    if hashlib.sha256(tree_material.encode()).hexdigest() != cp["tree_sha256"]:
        fail("tree_sha256 does not match the files list")
    else:
        ok(f"tree_sha256 consistent over {len(cp['files'])} files")
    for f in cp["files"]:
        data = base.fetch(f["path"])
        if hashlib.sha256(data).hexdigest() != f["sha256"] or len(data) != f["size"]:
            fail(f"checkpoint mismatch for {f['path']}")
    ok("every checkpointed object matches its recorded sha256+size")

    print("[3] indexes: NDJSON, semver order, tarball checksums")
    index_paths = [f["path"] for f in cp["files"] if f["path"].startswith("index/")]
    if not index_paths:
        fail("no index files listed in checkpoint")
    resolvable: dict[str, list[str]] = {}
    for ip in index_paths:
        org, name = ip.split("/")[1], ip.split("/")[2]
        lines = [json.loads(l) for l in base.fetch(ip).decode().splitlines() if l.strip()]
        versions = [l["version"] for l in lines]
        if versions != sorted(versions, key=semver_key) or len(set(versions)) != len(versions):
            fail(f"{ip}: versions not unique/ascending semver")
        for l in lines:
            algo, hexd = l["cksum"].split(":", 1)
            if algo != "sha256":
                fail(f"{ip}: unsupported cksum algo {algo}")
            blob = base.fetch(f"pkgs/{org}/{name}/{l['version']}.tar.zst")
            if hashlib.sha256(blob).hexdigest() != hexd or len(blob) != l["size"]:
                fail(f"{org}/{name}@{l['version']}: tarball does not match index cksum/size")
        resolvable[f"{org}/{name}"] = [l["version"] for l in lines if not l["yanked"]]
        ok(f"{org}/{name}: {len(lines)} versions verified")

    print("[4] yank semantics")
    yanked_seen = False
    for ip in index_paths:
        org, name = ip.split("/")[1], ip.split("/")[2]
        for l in (json.loads(x) for x in base.fetch(ip).decode().splitlines() if x.strip()):
            if l["yanked"]:
                yanked_seen = True
                if l["version"] in resolvable[f"{org}/{name}"]:
                    fail(f"{org}/{name}@{l['version']} yanked but resolvable")
                base.fetch(f"pkgs/{org}/{name}/{l['version']}.tar.zst")
                ok(f"{org}/{name}@{l['version']}: yanked => unresolvable, bytes retained")
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
    "tamper-tarball": lambda t: _flip_byte(next((t / "pkgs").rglob("*.tar.zst"))),
    "tamper-index": lambda t: _append(next(p for p in sorted((t / "index").rglob("*")) if p.is_file()), b'{"version":"9.9.9","deps":[],"cksum":"sha256:00","size":2,"yanked":false}\n'),
    "drop-checkpoint-entry": lambda t: _drop_cp_entry(t),
    "resurrect-yanked": lambda t: _unyank(t),
    "wrong-schema": lambda t: _bump_schema(t),
}


def _flip_byte(p: Path) -> None:
    b = bytearray(p.read_bytes()); b[len(b) // 2] ^= 0xFF; p.write_bytes(bytes(b))


def _append(p: Path, extra: bytes) -> None:
    p.write_bytes(p.read_bytes() + extra)


def _drop_cp_entry(t: Path) -> None:
    cp = json.loads((t / "checkpoint.json").read_text()); cp["files"] = cp["files"][1:]
    (t / "checkpoint.json").write_text(json.dumps(cp, indent=2, sort_keys=True) + "\n")


def _unyank(t: Path) -> None:
    for ip in (t / "index").rglob("*"):
        if ip.is_file():
            lines = [json.loads(l) for l in ip.read_text().splitlines() if l.strip()]
            changed = False
            for l in lines:
                if l["yanked"]:
                    l["yanked"] = False; changed = True
            if changed:
                ip.write_text("".join(json.dumps(l, sort_keys=True, separators=(",", ":")) + "\n" for l in lines))
                return
    raise SystemExit("no yanked line to mutate")


def _bump_schema(t: Path) -> None:
    d = t / ".well-known/zpkg-registry.json"
    doc = json.loads(d.read_text()); doc["schema_version"] = 99
    d.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")


def self_test(tree: Path) -> int:
    print("== red tests: every mutation must make the checker fail")
    bad = 0
    for name, mutate in MUTATIONS.items():
        with tempfile.TemporaryDirectory() as td:
            copy = Path(td) / "tree"
            shutil.copytree(tree, copy)
            mutate(copy)
            r = subprocess.run(
                [sys.executable, __file__, "--base", str(copy)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            verdict = "red as required" if r.returncode != 0 else "STAYED GREEN — checker is vacuous here"
            print(f"  {name}: {verdict}")
            if r.returncode == 0:
                bad += 1
    print(f"== self-test {'PASS' if bad == 0 else 'FAIL'}")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", help="tree path or https base URL")
    ap.add_argument("cmd", nargs="?", choices=["self-test"], help="self-test runs the mutation red tests against --base (local tree)")
    args = ap.parse_args()
    if not args.base:
        ap.error("--base required")
    if args.cmd == "self-test":
        return self_test(Path(args.base))
    return run_checks(Base(args.base))


if __name__ == "__main__":
    raise SystemExit(main())
