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

BUNDLE = Path("portfolio-bundle")
OUTPUT = Path("portfolio-validated")
CREDENTIAL_RE = re.compile(
    rb"(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|"
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|xox[baprs]-[A-Za-z0-9-]{10,})"
)
CONFLICT_RE = re.compile(rb"^(?:<<<<<<<|=======|>>>>>>>)(?: |$)", re.MULTILINE)


def run(command: list[str], cwd: Path, timeout: int = 180) -> dict[str, Any]:
    env = os.environ.copy()
    for name in (
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "FLEET_GH_TOKEN",
        "CROSS_ORG_APP_ID",
        "CROSS_ORG_APP_PRIVATE_KEY",
    ):
        env.pop(name, None)
    env.update(
        {
            "CI": "true",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        }
    )
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "output": completed.stdout[-12000:],
    }


def safe_extract(archive: Path, destination: Path) -> Path:
    total_size = 0
    with tarfile.open(archive, "r:gz") as tf:
        members = tf.getmembers()
        if not members or len(members) > 2000:
            raise ValueError(f"unexpected archive member count: {len(members)}")
        roots: set[str] = set()
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ValueError(f"unsafe archive path: {member.name}")
            roots.add(path.parts[0])
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise ValueError(f"unsupported archive entry type: {member.name}")
            if member.isfile():
                total_size += member.size
        if len(roots) != 1:
            raise ValueError(f"archive must have one root directory, found {sorted(roots)}")
        if total_size > 60 * 1024 * 1024:
            raise ValueError(f"archive expands beyond 60 MiB: {total_size}")
        root_name = next(iter(roots))
        for member in members:
            target = destination.joinpath(*PurePosixPath(member.name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                source = tf.extractfile(member)
                if source is None:
                    raise ValueError(f"could not read archive member: {member.name}")
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(member.mode & 0o777)
        return destination / root_name


def scan_repository(root: Path) -> list[str]:
    reasons: list[str] = []
    required = [
        ".managed-by-test-org-factory",
        "dependency-contract.yaml",
        "test-plan.yaml",
        "scripts/readiness.py",
        "tests/test_generated_contract.py",
    ]
    for relative in required:
        target = root / relative
        if not target.is_file() or target.stat().st_size == 0:
            reasons.append(f"missing-required:{relative}")

    for path in root.rglob("*"):
        if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            continue
        try:
            data = path.read_bytes()
        except OSError as error:
            reasons.append(f"read-error:{path.relative_to(root)}:{error}")
            continue
        relative = path.relative_to(root).as_posix()
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


def validate_entry(entry: dict[str, Any]) -> dict[str, Any]:
    key = f"{entry['repo']}#{entry['number']}"
    archive = BUNDLE / entry["archive"]
    result: dict[str, Any] = {"key": key, "entry": entry, "checks": [], "reasons": []}
    try:
        data = archive.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry["archive_sha256"]:
            result["reasons"].append("archive-digest-mismatch")
            return result

        with tempfile.TemporaryDirectory(prefix="portfolio-") as temp:
            root = safe_extract(archive, Path(temp))
            result["reasons"].extend(scan_repository(root))
            if result["reasons"]:
                return result

            checks = [
                run(["python3", "-m", "compileall", "-q", "scripts", "tests"], root, 90),
                run(["python3", "-m", "pytest", "-q"], root, 240),
                run(["python3", "scripts/readiness.py", "--offline", "--strict"], root, 90),
            ]
            result["checks"] = checks
            for check in checks:
                if check["returncode"] != 0:
                    result["reasons"].append(
                        f"command-failed:{' '.join(check['command'])}:exit-{check['returncode']}"
                    )

            readiness_path = root / "artifacts/readiness-status.json"
            if not readiness_path.is_file():
                result["reasons"].append("missing-readiness-artifact")
            else:
                readiness = json.loads(readiness_path.read_text())
                result["readiness"] = readiness
                if readiness.get("declared_readiness") != "ready":
                    result["reasons"].append("declared-readiness-not-ready")
                if readiness.get("overall") != "ready":
                    result["reasons"].append("offline-readiness-not-ready")

            if (root / "package.json").is_file():
                package = run(
                    [
                        "node",
                        "-e",
                        "JSON.parse(require('node:fs').readFileSync('package.json','utf8'));",
                    ],
                    root,
                    30,
                )
                result["checks"].append(package)
                if package["returncode"] != 0:
                    result["reasons"].append("invalid-package-json")

            result["reasons"] = sorted(set(result["reasons"]))
            result["validated"] = not result["reasons"]
            return result
    except Exception as error:  # noqa: BLE001
        result["reasons"].append(f"exception:{type(error).__name__}:{error}")
        result["validated"] = False
        return result


def main() -> int:
    manifest = json.loads((BUNDLE / "manifest.json").read_text())
    entries = manifest.get("entries", [])
    OUTPUT.mkdir(parents=True, exist_ok=True)
    workers = min(8, max(1, len(entries)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(validate_entry, entries))

    validated = [result["entry"] for result in results if result.get("validated")]
    failed = [result for result in results if not result.get("validated")]
    document = {
        "schema_version": 1,
        "validated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "entries": validated,
        "failed_count": len(failed),
    }
    (OUTPUT / "validated.json").write_text(json.dumps(document, indent=2) + "\n")
    (OUTPUT / "validation-results.json").write_text(json.dumps(results, indent=2) + "\n")

    print(
        "PORTFOLIO_VALIDATION "
        + json.dumps(
            {
                "input": len(entries),
                "validated": len(validated),
                "failed": len(failed),
            }
        )
    )
    for result in failed:
        print(f"VALIDATION_FAILURE {result['key']} {';'.join(result['reasons'])}")
        for check in result.get("checks", []):
            if check["returncode"] != 0:
                print(f"CHECK_OUTPUT {result['key']} {' '.join(check['command'])}\n{check['output']}")

    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        lines = [
            "## Credential-free ready portfolio validation",
            "",
            f"- Exact archives supplied: {len(entries)}",
            f"- Independently validated: {len(validated)}",
            f"- Failed validation: {len(failed)}",
            "",
        ]
        lines.extend(
            f"- {result['key']}: {', '.join(result['reasons'][:6])}"
            for result in failed[:40]
        )
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    return 0 if validated else 1


if __name__ == "__main__":
    raise SystemExit(main())
