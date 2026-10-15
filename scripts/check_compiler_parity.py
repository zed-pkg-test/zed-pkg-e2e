#!/usr/bin/env python3
"""Fail-closed formatter/real-compiler acceptance differential harness.

Compiler commands must be supplied explicitly: no compiler is downloaded or
silently mocked in production. A compiler rejecting both forms is UNSUPPORTED,
not proof of equivalent semantics or a successful parser round-trip.
"""
import argparse
import json
import os
import selectors
import signal
import stat
import shlex
import subprocess
import tempfile
import time
from pathlib import Path


# Bound untrusted fixture snapshots and formatter stdout even when callers
# supply a very large --max-case-bytes value.
MAX_CASE_BYTES_LIMIT = 8 * 1024 * 1024


def command_argv(template, source):
    argv = [word.replace("{source}", str(source)) for word in shlex.split(template)]
    if not any("{source}" in word for word in shlex.split(template)):
        raise ValueError("compiler command must contain {source}")
    if not argv:
        raise ValueError("empty compiler command")
    return argv


def _kill_process_group(process):
    """Stop the direct child and descendants that inherited its process group."""
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif process.poll() is None:
            process.kill()
    except ProcessLookupError:
        pass


def run_process(argv, timeout, stdout_limit=None):
    """Capture bounded output and diagnostics; kill a producer at its byte limit.

    stdout_limit=None discards stdout for compiler admission commands.
    Neither stdout nor stderr is ever spooled without an active byte bound.
    """
    # shlex.split("") produces an empty argv. Popen([]) raises IndexError,
    # which used to escape the JSON/failed-gate path entirely.
    if not argv:
        return "unavailable", None, b"", b"empty process command"
    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if stdout_limit is not None else subprocess.DEVNULL,
            stderr=subprocess.PIPE, start_new_session=(os.name == "posix"),
        )
    except OSError as exc:
        return "unavailable", None, b"", str(exc).encode("utf-8", errors="replace")

    output = bytearray()
    diagnostics = bytearray()
    deadline = time.monotonic() + timeout
    status = "exited"
    # Initialize the selector inside the guarded region: allocation itself may
    # fail *after* the subprocess has started, and must not leak that child.
    selector = None
    reaped_normally = False
    try:
        selector = selectors.DefaultSelector()
        streams = []
        if process.stdout is not None:
            streams.append(("stdout", process.stdout))
        streams.append(("stderr", process.stderr))
        for name, stream in streams:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)

        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                status = "timeout"
                break
            ready = selector.select(remaining)
            if not ready:
                status = "timeout"
                break
            for key, _ in ready:
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                if key.data == "stdout":
                    if len(output) + len(chunk) > stdout_limit:
                        status = "output_limit"
                        break
                    output.extend(chunk)
                elif len(diagnostics) < 4000:
                    diagnostics.extend(chunk[:4000 - len(diagnostics)])
            if status != "exited":
                break
        if status == "exited":
            remaining = deadline - time.monotonic()
            try:
                process.wait(timeout=max(0, remaining))
                reaped_normally = True
            except subprocess.TimeoutExpired:
                status = "timeout"
    except (OSError, ValueError) as exc:
        status = "unavailable"
        diagnostics = bytearray(str(exc).encode("utf-8", errors="replace")[:4000])
    finally:
        # Any incomplete path, including KeyboardInterrupt, must also stop
        # children that inherited the process group and our output pipes.
        if not reaped_normally:
            _kill_process_group(process)
        if process.poll() is None:
            process.kill()
        process.wait()
        if selector is not None:
            selector.close()
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()

    return status, process.returncode, bytes(output), bytes(diagnostics)


def execute(argv, timeout):
    status, returncode, _, diagnostics = run_process(argv, timeout)
    if status != "exited":
        return status, diagnostics
    if returncode == 0:
        return "accept", diagnostics
    if returncode == 1:
        return "reject", diagnostics
    if returncode == 2:
        return "usage", diagnostics
    if returncode < 0:
        return "crashed", diagnostics
    return "error", diagnostics

def audit_case(path, formatter, java_command, llvm_command, timeout,
               max_case_bytes=1024 * 1024):
    # Never submit symlinks, special files or unbounded fixtures to compilers.
    if not (0 < max_case_bytes <= MAX_CASE_BYTES_LIMIT):
        return {"case": path.name, "ok": False, "fixture": "invalid",
                "detail": "max-case-bytes exceeds the safe harness limit"}
    # Check both reported size and actual bytes to catch files growing mid-read.
    try:
        # Reject symlinks at open time, not only in a racy prior stat call.
        # O_NONBLOCK prevents a swapped-in FIFO from hanging the harness.
        if path.is_symlink():
            raise ValueError("fixture is a symlink")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as source:
            details = os.fstat(source.fileno())
            if not stat.S_ISREG(details.st_mode):
                raise ValueError("fixture is not a regular file")
            if details.st_size > max_case_bytes:
                raise ValueError("fixture exceeds byte limit")
            original = source.read(max_case_bytes + 1)
        if len(original) > max_case_bytes:
            raise ValueError("fixture exceeds byte limit")
        original.decode("utf-8")
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return {"case": path.name, "ok": False,
                "fixture": "invalid", "detail": str(exc)}
    with tempfile.TemporaryDirectory(prefix="oresfmt-parity-") as directory:
        root = Path(directory)
        original_path = root / "original" / path.name
        formatted_path = root / "formatted" / path.name
        original_path.parent.mkdir()
        formatted_path.parent.mkdir()
        original_path.write_bytes(original)
        # Drain pipes in bounded chunks and terminate a flood immediately.
        format_status, exit_code, formatted_bytes, errors = run_process(
            formatter + ["--stdout", str(original_path)],
            timeout, stdout_limit=max_case_bytes * 2,
        )
        if format_status == "output_limit":
            return {"case": path.name, "ok": False,
                    "formatter": "invalid_output"}
        if format_status != "exited":
            return {"case": path.name, "ok": False,
                    "formatter": "unavailable", "detail": format_status}
        if exit_code != 0:
            return {"case": path.name, "ok": False,
                    "formatter": "reject",
                    "detail": errors.decode("utf-8", errors="replace")}

        # A broken formatter must not erase a program or inject malformed text
        # and be rescued by permissive compiler command-line wrappers.
        if original.strip() and not formatted_bytes.strip():
            return {"case": path.name, "ok": False,
                    "formatter": "invalid_output"}
        try:
            formatted_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return {"case": path.name, "ok": False,
                    "formatter": "invalid_utf8"}
        formatted_path.write_bytes(formatted_bytes)

        # A second pass must be byte-identical and independently bounded.
        second_status, second_exit, second_bytes, _ = run_process(
            formatter + ["--stdout", str(formatted_path)],
            timeout, stdout_limit=max_case_bytes * 2,
        )
        stable = (second_status == "exited" and second_exit == 0
                  and second_bytes == formatted_bytes)
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
    parser.add_argument("--max-case-bytes", type=int, default=1024 * 1024,
                        help="maximum input fixture bytes (default: 1 MiB)")
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
    if args.max_case_bytes <= 0:
        parser.error("max-case-bytes must be positive")
    if args.max_case_bytes > MAX_CASE_BYTES_LIMIT:
        parser.error(f"max-case-bytes must not exceed {MAX_CASE_BYTES_LIMIT}")
    results = [audit_case(path, shlex.split(args.formatter),
                          args.java_command, args.llvm_command, args.timeout,
                          args.max_case_bytes)
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
