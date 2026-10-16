"""Offline tests: no Rust toolchain or source repositories are needed."""
from __future__ import annotations

import importlib.util
import hashlib
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_formatter_workspace.py"
spec = importlib.util.spec_from_file_location("formatter_audit", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class WorkspaceAuditTests(unittest.TestCase):
    def make_workspace(self, base: Path, code: str):
        roots = {}
        for repo in module.REPOSITORIES:
            root = base / repo
            root.mkdir()
            (root / "test.ores").write_bytes(b"original\n")
            roots[repo] = root
        binary = base / "formatter"
        binary.write_text("#!/usr/bin/python3\n" + code)
        binary.chmod(0o755)
        return roots, binary

    def test_runs_only_against_temporary_copies_and_reports_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots, binary = self.make_workspace(Path(tmp),
                "import pathlib,sys\n"
                "path=pathlib.Path(sys.argv[-1])\n"
                "data=path.read_bytes()\n"
                "sys.stdout.buffer.write(data.replace(b'original',b'formatted'))\n")
            report = module.audit(roots, binary)
            self.assertEqual(report["total"], 7)
            self.assertEqual(report["counts"]["formatted"], 7)
            self.assertEqual(report["would_change"], 7)
            for root in roots.values():
                self.assertEqual((root / "test.ores").read_bytes(), b"original\n")

    def test_detects_non_idempotent_formatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots, binary = self.make_workspace(Path(tmp),
                "import pathlib,sys\n"
                "sys.stdout.buffer.write(pathlib.Path(sys.argv[-1]).read_bytes()+b'!')\n")
            report = module.audit(roots, binary)
            self.assertEqual(report["counts"]["unstable"], 7)

    def test_detects_formatter_mutating_temporary_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots, binary = self.make_workspace(Path(tmp),
                "import pathlib,sys\n"
                "path=pathlib.Path(sys.argv[-1])\n"
                "path.write_bytes(b'corrupt')\n"
                "sys.stdout.buffer.write(b'corrupt')\n")
            report = module.audit(roots, binary)
            self.assertEqual(report["counts"]["unsafe_temp_write"], 7)
            for root in roots.values():
                self.assertEqual((root / "test.ores").read_bytes(), b"original\n")

    def test_oversized_formatter_stdout_is_bounded_and_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots, binary = self.make_workspace(Path(tmp),
                "import sys\n"
                "sys.stdout.buffer.write(b'x' * 8192)\n")
            report = module.audit(roots, binary, max_bytes=16)
            self.assertEqual(report["counts"]["oversize_output"], 7)
            self.assertEqual(report["counts"]["formatted"], 0)
            for root in roots.values():
                self.assertEqual((root / "test.ores").read_bytes(), b"original\n")

    def test_hung_formatter_is_terminated(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots, binary = self.make_workspace(Path(tmp),
                "import time\n"
                "time.sleep(2)\n")
            report = module.audit(roots, binary, timeout=0.05)
            self.assertEqual(report["counts"]["timeout"], 7)

    def test_oversized_input_has_no_misleading_partial_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots, binary = self.make_workspace(Path(tmp),
                "import pathlib,sys\n"
                "sys.stdout.buffer.write(pathlib.Path(sys.argv[-1]).read_bytes())\n")
            report = module.audit(roots, binary, max_bytes=3)
            self.assertEqual(report["counts"]["oversize"], 7)
            for result in report["results"]:
                self.assertIsNone(result["input_sha256"])

    def test_usage_exit_is_tool_error_even_in_inventory_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots, binary = self.make_workspace(Path(tmp),
                "import sys\n"
                "sys.exit(2)\n")
            report = module.audit(roots, binary)
            self.assertEqual(report["counts"]["tool_error"], 7)
            self.assertEqual(report["counts"]["rejected"], 0)

    def test_snapshot_rejects_symlink_and_special_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "case.ores"
            source.write_bytes(b"safe\n")
            alias = root / "alias.ores"
            alias.symlink_to(source)
            with self.assertRaises(ValueError):
                module.snapshot_source(alias, 100)
            self.assertEqual(module.snapshot_source(source, 100), (b"safe\n", False))
            if hasattr(__import__("os"), "mkfifo"):
                import os
                pipe = root / "fifo.ores"
                os.mkfifo(pipe)
                # O_NONBLOCK avoids waiting for a FIFO writer while fstat
                # rejects the nonregular descriptor before source.read().
                with self.assertRaises(ValueError):
                    module.snapshot_source(pipe, 100)

    def test_formatter_replacement_of_temporary_input_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots, binary = self.make_workspace(Path(tmp),
                "from pathlib import Path\n"
                "import sys\n"
                "path=Path(sys.argv[-1])\n"
                "path.unlink()\n"
                "path.symlink_to('/etc/passwd')\n"
                "print('formatted')\n")
            report = module.audit(roots, binary)
            self.assertEqual(report["counts"]["unsafe_temp_write"], 7)
            for root in roots.values():
                self.assertEqual((root / "test.ores").read_bytes(), b"original\n")

    def test_rejects_missing_roots_and_ignores_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots, binary = self.make_workspace(Path(tmp),
                "import pathlib,sys\n"
                "sys.stdout.buffer.write(pathlib.Path(sys.argv[-1]).read_bytes())\n")
            source = roots[module.REPOSITORIES[0]] / "test.ores"
            source.with_name("alias.ores").symlink_to(source)
            self.assertEqual(len(module.discover(source.parent)), 1)
            del roots[module.REPOSITORIES[-1]]
            with self.assertRaisesRegex(ValueError, "missing repository root"):
                module.audit(roots, binary)


    def test_formatter_must_not_erase_real_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots, binary = self.make_workspace(Path(tmp),
                "import sys\n"
                "sys.stdout.buffer.write(b'  \\n')\n")
            report = module.audit(roots, binary)
            self.assertEqual(report["counts"]["invalid_output"], 7)
            self.assertEqual(report["counts"]["formatted"], 0)

    def test_formatter_must_not_emit_invalid_utf8(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots, binary = self.make_workspace(Path(tmp),
                "import sys\n"
                "sys.stdout.buffer.write(bytes([255]))\n")
            report = module.audit(roots, binary)
            self.assertEqual(report["counts"]["invalid_utf8"], 7)

    def test_invalid_utf8_source_is_not_submitted_to_formatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots, binary = self.make_workspace(Path(tmp),
                "import sys\n"
                "sys.stdout.buffer.write(b'never')\n")
            for root in roots.values():
                (root / "test.ores").write_bytes(bytes([255]))
            report = module.audit(roots, binary)
            self.assertEqual(report["counts"]["invalid_source"], 7)
            self.assertEqual(report["counts"]["formatted"], 0)

    def test_no_follow_reader_rejects_leaf_symlinks_and_fifo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real.ores"
            real.write_bytes(b"allowed\n")
            linked = root / "linked.ores"
            linked.symlink_to(real)
            with self.assertRaises(ValueError):
                module.snapshot_source(linked, 1024)
            if hasattr(os, "mkfifo"):
                fifo = root / "pipe.ores"
                os.mkfifo(fifo)
                with self.assertRaisesRegex(ValueError, "regular"):
                    module.snapshot_source(fifo, 1024)

    def test_formatter_digest_streams_and_formatted_output_is_hashed(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots, binary = self.make_workspace(Path(tmp),
                "import pathlib,sys\n"
                "sys.stdout.buffer.write(pathlib.Path(sys.argv[-1]).read_bytes())\n")
            report = module.audit(roots, binary)
            self.assertEqual(report["formatter_sha256"],
                             hashlib.sha256(binary.read_bytes()).hexdigest())
            self.assertEqual(report["counts"]["formatted"], 7)
            for item in report["results"]:
                self.assertEqual(item["output_sha256"], item["input_sha256"])



if __name__ == "__main__":
    unittest.main()
