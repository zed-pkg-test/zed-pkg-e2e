#!/usr/bin/env python3
"""Read-only cross-repository Oreslang formatter compatibility inventory.

Every formatter invocation receives an isolated temporary copy, never a source
path from a Git worktree. This is not a semantic equivalence/compile test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import selectors
import signal
import stat
import subprocess
import tempfile
import time
from pathlib import Path

REPOSITORIES = (
    "oreslang-source.java", "oreslang-source.llvm", "oreslang-source.wasm",
    "oreslang-source.forge", "oreslang-source.net", "oreslang-common-source",
    "oreslang-format",
)
IGNORE_DIRS = {".git", "build", "target", "out", "node_modules", "__pycache__"}


def discover(root: Path) -> list[Path]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"repository root must be a real directory: {root}")
    files = []
    for parent, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in IGNORE_DIRS and not (Path(parent) / d).is_symlink())
        for name in sorted(names):
            file = Path(parent) / name
            if file.suffix == ".ores" and file.is_file() and not file.is_symlink():
                files.append(file)
    return files



def read_regular_bounded(path: Path, limit: int) -> bytes:
    """Refuse swapped symlinks/FIFOs/devices and read no more than limit+1.

    O_NONBLOCK avoids blocking on a FIFO substituted after discovery.
    O_NOFOLLOW prevents a leaf symlink from being followed during open on
    systems that support it. Parent-directory confinement is separate.
    """
    if path.is_symlink():
        raise ValueError("source path is a symlink")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError("source path is not a regular file")
        return source.read(limit + 1)


def sha256_file(path: Path) -> str:
    """Compute the executable's digest without loading it all into RAM."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def formatter_pass(binary: Path, source: bytes, timeout: float,
                   max_output_bytes: int = 2097152) -> dict:
    # Do not use subprocess.run(capture_output=True): untrusted formatter
    # output could exhaust RAM long before the timeout fires.
    with tempfile.TemporaryDirectory(prefix="oresfmt-audit-") as scratch:
        path = Path(scratch) / "input.ores"
        path.write_bytes(source)
        try:
            process = subprocess.Popen(
                [str(binary), "--stdout", str(path)], cwd=scratch,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, start_new_session=(os.name == "posix"),
            )
        except OSError as exc:
            return {"status": "tool_error", "detail": str(exc)[:200]}

        output = bytearray()
        errors = bytearray()
        status = "finished"
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            try:
                for name, stream in (("stdout", process.stdout),
                                     ("stderr", process.stderr)):
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
                            if len(output) + len(chunk) > max_output_bytes:
                                status = "oversize_output"
                                break
                            output.extend(chunk)
                        elif len(errors) < 200:
                            errors.extend(chunk[:200 - len(errors)])
                    if status != "finished":
                        break
                if status == "finished":
                    try:
                        process.wait(timeout=max(0, deadline - time.monotonic()))
                    except subprocess.TimeoutExpired:
                        status = "timeout"
            except (OSError, ValueError) as exc:
                status = "tool_error"
                errors = bytearray(str(exc).encode("utf-8", "replace")[:200])
            finally:
                if status != "finished":
                    try:
                        if os.name == "posix":
                            os.killpg(process.pid, signal.SIGKILL)
                        elif process.poll() is None:
                            process.kill()
                    except ProcessLookupError:
                        pass
                if process.poll() is None:
                    process.kill()
                process.wait()
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()

        if path.read_bytes() != source:
            return {"status": "unsafe_temp_write",
                    "detail": "formatter changed temporary input"}
        if status != "finished":
            return {"status": status, "detail": errors.decode("utf-8", "replace")}
        if process.returncode != 0:
            # Only the formatter's documented source-rejection exit (1) is
            # an unsupported fixture. Usage errors, crashes and other
            # failures must block even a non-strict inventory run.
            return {"status": "rejected" if process.returncode == 1 else "tool_error",
                    "detail": errors.decode("utf-8", "replace"),
                    "exit_code": process.returncode}
        if source.strip() and not output.strip():
            return {"status": "invalid_output",
                    "detail": "formatter erased nonblank source"}
        formatted = bytes(output)
        try:
            formatted.decode("utf-8")
        except UnicodeDecodeError:
            return {"status": "invalid_utf8",
                    "detail": "formatter returned non-UTF-8 output"}
        return {"status": "formatted", "output": formatted}


def audit(roots: dict[str, Path], formatter: Path, timeout: float = 15,
          max_bytes: int = 1048576) -> dict:
    if max_bytes <= 0 or timeout <= 0:
        raise ValueError("max_bytes and timeout must be positive")
    results = []
    for repo in REPOSITORIES:
        if repo not in roots:
            raise ValueError(f"missing repository root: {repo}")
        root = roots[repo]
        files = discover(root)
        if not files:
            raise ValueError(f"no .ores source files under {repo}")
        for path in files:
            item = {"repository": repo, "path": path.relative_to(root).as_posix(),
                    "input_sha256": None}
            try:
                original = read_regular_bounded(path, max_bytes)
            except (OSError, ValueError) as exc:
                item.update(status="unsafe_source", detail=str(exc)[:200])
                results.append(item)
                continue
            oversized = len(original) > max_bytes
            if oversized:
                item.update(status="oversize", detail="file exceeds audit size limit")
            else:
                item["input_sha256"] = hashlib.sha256(original).hexdigest()
                try:
                    original.decode("utf-8")
                except UnicodeDecodeError:
                    item.update(status="invalid_source",
                                detail="source is not valid UTF-8")
                else:
                    first = formatter_pass(formatter, original, timeout, max_bytes * 2)
                    item["status"] = first["status"]
                    if first["status"] == "formatted":
                        output = first["output"]
                        item["output_sha256"] = hashlib.sha256(output).hexdigest()
                        second = formatter_pass(formatter, output, timeout, max_bytes * 2)
                        item["would_change"] = output != original
                        item["idempotent"] = (second["status"] == "formatted"
                                              and second["output"] == output)
                        if not item["idempotent"]:
                            item["status"] = "unstable"
                            item["detail"] = second.get("detail", "second pass changed output")
                    else:
                        item["detail"] = first.get("detail", "")
                        if "exit_code" in first:
                            item["exit_code"] = first["exit_code"]
            # Re-open with the same no-follow and regular-file requirements.
            # A source swapped during audit is a blocking observation.
            if not oversized:
                try:
                    if read_regular_bounded(path, max_bytes) != original:
                        item["status"] = "source_changed"
                except (OSError, ValueError):
                    item["status"] = "source_changed"
            results.append(item)
    counts = {kind: sum(item["status"] == kind for item in results) for kind in
              ("formatted", "rejected", "unstable", "tool_error", "unsafe_temp_write",
               "source_changed", "oversize", "oversize_output", "timeout",
               "unsafe_source", "invalid_source", "invalid_output", "invalid_utf8")}
    return {"repositories": list(REPOSITORIES), "total": len(results), "counts": counts,
            "would_change": sum(bool(x.get("would_change")) for x in results),
            "formatter_sha256": sha256_file(formatter),
            "results": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True,
                        help="parent directory containing all seven repositories")
    parser.add_argument("--formatter", type=Path, required=True)
    parser.add_argument("--formatter-sha256", help="optional expected reviewed formatter SHA-256")
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--strict", action="store_true",
                        help="fail on any rejection, change, or unstable output; use curated corpus")
    args = parser.parse_args(argv)
    if args.timeout <= 0 or args.timeout > 300:
        parser.error("--timeout must be between 0 and 300 seconds")
    try:
        binary = args.formatter.resolve(strict=True)
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ValueError("formatter must be an executable regular file")
        digest = sha256_file(binary)
        if args.formatter_sha256 and digest != args.formatter_sha256.lower():
            raise ValueError("formatter SHA-256 mismatch")
        workspace = args.workspace.resolve(strict=True)
        roots = {name: workspace / name for name in REPOSITORIES}
        report = audit(roots, binary, args.timeout)
        if report["formatter_sha256"] != digest:
            raise ValueError("formatter binary changed during audit")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    report["formatter_pinned"] = bool(args.formatter_sha256)
    print(json.dumps(report, indent=2))
    unsafe = any(report["counts"][name] for name in
                 ("unstable", "tool_error", "unsafe_temp_write", "source_changed", "oversize", "oversize_output", "timeout",
                  "unsafe_source", "invalid_source", "invalid_output", "invalid_utf8"))
    if args.strict:
        unsafe |= bool(report["counts"]["rejected"] or report["would_change"])
    return int(unsafe)


if __name__ == "__main__":
    raise SystemExit(main())
