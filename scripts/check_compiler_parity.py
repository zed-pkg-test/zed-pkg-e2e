#!/usr/bin/env python3
"""Fail-closed formatter/real-compiler acceptance differential harness.

Compiler commands must be supplied explicitly: no compiler is downloaded or
silently mocked in production. A compiler rejecting both forms is UNSUPPORTED,
not proof of equivalent semantics or a successful parser round-trip.
"""
import argparse
import json
import shlex
import subprocess
import tempfile
from pathlib import Path


def command_argv(template, source):
    argv = [word.replace("{source}", str(source)) for word in shlex.split(template)]
    if not any("{source}" in word for word in shlex.split(template)):
        raise ValueError("compiler command must contain {source}")
    if not argv:
        raise ValueError("empty compiler command")
    return argv


def execute(argv, timeout):
    try:
        result = subprocess.run(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return "timeout", b""
    except OSError as exc:
        return "unavailable", str(exc).encode("utf-8", errors="replace")
    if result.returncode == 0:
        status = "accept"
    elif result.returncode == 1:
        status = "reject"
    elif result.returncode == 2:
        # Java --check and LLVM compiler CLI reserve this for usage errors.
        status = "usage"
    elif result.returncode < 0:
        status = "crashed"
    else:
        status = "error"
    return status, result.stderr[:4000]


def audit_case(path, formatter, java_command, llvm_command, timeout):
    original = path.read_bytes()
    with tempfile.TemporaryDirectory(prefix="oresfmt-parity-") as directory:
        root = Path(directory)
        original_path = root / "original" / path.name
        formatted_path = root / "formatted" / path.name
        original_path.parent.mkdir()
        formatted_path.parent.mkdir()
        original_path.write_bytes(original)
        try:
            formatted = subprocess.run(
                formatter + ["--stdout", str(original_path)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=timeout, check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return {"case": path.name, "ok": False,
                    "formatter": "unavailable", "detail": str(exc)}
        if formatted.returncode != 0:
            return {"case": path.name, "ok": False,
                    "formatter": "reject",
                    "detail": formatted.stderr[:4000].decode(
                        "utf-8", errors="replace")}
        formatted_path.write_bytes(formatted.stdout)

        # A second formatter pass must be identical before any compiler checks.
        try:
            second = subprocess.run(
                formatter + ["--stdout", str(formatted_path)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=timeout, check=False,
            )
            stable = second.returncode == 0 and second.stdout == formatted.stdout
        except (subprocess.TimeoutExpired, OSError):
            stable = False
        statuses = {}
        for backend, command in (("java", java_command), ("llvm", llvm_command)):
            before, _ = execute(command_argv(command, original_path), timeout)
            after, _ = execute(command_argv(command, formatted_path), timeout)
            statuses[backend] = {"original": before, "formatted": after}
        ok = stable and all(
            result["original"] == result["formatted"]
            and result["original"] in ("accept", "reject")
            for result in statuses.values()
        )
        return {"case": path.name, "ok": ok, "idempotent": stable,
                "backends": statuses,
                "backend_agreement": (statuses["java"]["original"] ==
                                      statuses["llvm"]["original"] and
                                      statuses["java"]["formatted"] ==
                                      statuses["llvm"]["formatted"]),
                "shared_supported": all(
                    status["original"] == "accept"
                    for status in statuses.values()
                )}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formatter", required=True,
                        help="formatter command, e.g. './target/debug/oresfmt'")
    parser.add_argument("--java-command", required=True,
                        help="Java compiler command with {source} placeholder")
    parser.add_argument("--llvm-command", required=True,
                        help="LLVM compiler command with {source} placeholder")
    parser.add_argument("--cases", type=Path,
                        default=Path(__file__).resolve().parent.parent /
                        "tests" / "compiler_parity_cases")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--allow-zero-shared-accepted", action="store_true",
                        help="diagnostic only: permit no jointly accepted fixtures")
    parser.add_argument("--allow-backend-divergence", action="store_true",
                        help="diagnostic only: permit Java/LLVM admission mismatch")
    args = parser.parse_args(argv)

    cases = sorted(args.cases.glob("*.ores"))
    if not cases:
        parser.error("no .ores cases were found; refusing a vacuous pass")
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    results = [audit_case(path, shlex.split(args.formatter),
                          args.java_command, args.llvm_command, args.timeout)
               for path in cases]
    shared = sum(case.get("shared_supported", False) for case in results)
    ok = all(case["ok"] for case in results)
    if shared == 0 and not args.allow_zero_shared_accepted:
        ok = False
    if not args.allow_backend_divergence and any(
        case.get("backend_agreement") is False for case in results
    ):
        ok = False
    print(json.dumps({"ok": ok, "shared_accepted": shared,
                      "backend_agreement_required": not args.allow_backend_divergence,
                      "results": results}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
