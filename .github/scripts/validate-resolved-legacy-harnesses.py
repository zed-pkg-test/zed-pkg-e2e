#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

BUNDLE = Path("legacy-harness-bundle")
OUTPUT = Path("legacy-harness-validated")
CREDENTIAL_RE = re.compile(rb"(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|xox[baprs]-[A-Za-z0-9-]{10,})")
CONFLICT_RE = re.compile(rb"^(?:<<<<<<<|=======|>>>>>>>)(?: |$)", re.MULTILINE)


def safe_extract(archive: Path, destination: Path) -> Path:
    with tarfile.open(archive, "r:gz") as tf:
        members = tf.getmembers()
        roots = {PurePosixPath(member.name).parts[0] for member in members if PurePosixPath(member.name).parts}
        if len(roots) != 1 or len(members) > 2000:
            raise ValueError("unexpected archive shape")
        total = 0
        for member in members:
            parts = PurePosixPath(member.name).parts
            if not parts or PurePosixPath(member.name).is_absolute() or ".." in parts:
                raise ValueError(f"unsafe archive path: {member.name}")
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise ValueError(f"unsupported archive entry: {member.name}")
            if member.isfile():
                total += member.size
        if total > 60 * 1024 * 1024:
            raise ValueError("archive too large")
        for member in members:
            target = destination.joinpath(*PurePosixPath(member.name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                source = tf.extractfile(member)
                if source is None:
                    raise ValueError(f"unreadable archive member: {member.name}")
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(member.mode & 0o777)
        return destination / next(iter(roots))


def run(command: list[str], cwd: Path, timeout: int = 180) -> dict[str, Any]:
    env = os.environ.copy()
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "FLEET_GH_TOKEN"):
        env.pop(name, None)
    env.update({"CI": "true", "NODE_OPTIONS": "--disable-proto=throw", "GIT_TERMINAL_PROMPT": "0"})
    completed = subprocess.run(command, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, check=False)
    return {"command": command, "returncode": completed.returncode, "output": completed.stdout[-12000:]}


def scan(root: Path) -> list[str]:
    reasons: list[str] = []
    for relative in ("test-plan.json", "scripts/validate-plan.mjs"):
        target = root / relative
        if not target.is_file() or target.stat().st_size == 0:
            reasons.append(f"missing-required:{relative}")
    for file in root.rglob("*"):
        if not file.is_file() or file.stat().st_size > 2 * 1024 * 1024:
            continue
        data = file.read_bytes()
        relative = file.relative_to(root).as_posix()
        if CONFLICT_RE.search(data):
            reasons.append(f"conflict-marker:{relative}")
        if CREDENTIAL_RE.search(data):
            reasons.append(f"credential-pattern:{relative}")
        if relative.startswith(".github/workflows/") and relative.endswith((".yml", ".yaml")):
            text = data.decode("utf-8", errors="replace")
            if re.search(r"^\s*pull_request_target\s*:", text, re.MULTILINE):
                reasons.append(f"pull-request-target:{relative}")
            if re.search(r"^\s*permissions\s*:\s*write-all\s*$", text, re.MULTILINE):
                reasons.append(f"write-all:{relative}")
            if re.search(r"(?:curl|wget)[^\n|]*\|\s*(?:ba|z|fi)?sh\b", text, re.IGNORECASE):
                reasons.append(f"remote-shell-pipe:{relative}")
            if re.search(r"\beval\s+[\"']?\$", text):
                reasons.append(f"eval-shell:{relative}")
            for use in re.findall(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)\s*$", text, re.MULTILINE):
                if use.startswith("./"):
                    continue
                action, separator, ref = use.rpartition("@")
                immutable = bool(re.fullmatch(r"[0-9a-fA-F]{40}", ref))
                trusted_major = bool(re.match(r"^(actions|github)/", action) and re.fullmatch(r"v\d+", ref))
                if not separator or not (immutable or trusted_major):
                    reasons.append(f"untrusted-unpinned-action:{relative}:{use}")
    return sorted(set(reasons))


def validate(entry: dict[str, Any]) -> dict[str, Any]:
    key = f"{entry['repo']}#{entry['number']}"
    result: dict[str, Any] = {"key": key, "entry": entry, "checks": [], "reasons": []}
    try:
        archive = BUNDLE / entry["archive"]
        data = archive.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["archive_sha256"]:
            result["reasons"].append("archive-digest-mismatch")
            return result
        with tempfile.TemporaryDirectory(prefix="legacy-harness-") as temp:
            root = safe_extract(archive, Path(temp))
            result["reasons"].extend(scan(root))
            if result["reasons"]:
                return result
            plan = json.loads((root / "test-plan.json").read_text())
            serialized = json.dumps(plan).lower()
            if "planned_dependency" in serialized or "unresolved" in serialized:
                result["reasons"].append("plan-not-fully-resolved")
            check = run(["node", "scripts/validate-plan.mjs"], root, 180)
            result["checks"].append(check)
            if check["returncode"] != 0:
                result["reasons"].append(f"validate-plan-failed:{check['returncode']}")
            result["validated"] = not result["reasons"]
            return result
    except Exception as error:  # noqa: BLE001
        result["reasons"].append(f"exception:{type(error).__name__}:{error}")
        result["validated"] = False
        return result


def main() -> int:
    manifest = json.loads((BUNDLE / "manifest.json").read_text())
    entries = manifest.get("entries", [])
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, max(1, len(entries)))) as executor:
        results = list(executor.map(validate, entries))
    passed = [result["entry"] for result in results if result.get("validated")]
    failed = [result for result in results if not result.get("validated")]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "validated.json").write_text(json.dumps({"schema_version": 1, "entries": passed}, indent=2) + "\n")
    (OUTPUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print("LEGACY_HARNESS_VALIDATION " + json.dumps({"input": len(entries), "validated": len(passed), "failed": len(failed)}))
    for result in failed:
        print(f"VALIDATION_FAILURE {result['key']} {';'.join(result['reasons'])}")
        for check in result.get("checks", []):
            if check["returncode"] != 0:
                print(f"CHECK_OUTPUT {result['key']}\n{check['output']}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
