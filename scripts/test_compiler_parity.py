"""Offline harness self-tests; these are NOT Java/LLVM compiler parity evidence."""
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from check_compiler_parity import audit_case, command_argv, execute, main, run_process


class ParityHarnessTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.path = Path(self.root.name)
        self.formatter = self.path / "formatter.py"
        self.compiler = self.path / "compiler.py"
        self.formatter.write_text(
            "import sys\nfrom pathlib import Path\n"
            "p=Path(sys.argv[-1])\n"
            "sys.stdout.write(p.read_text().replace('  ', ' '))\n",
            encoding="utf-8",
        )
        self.compiler.write_text(
            "import sys\nfrom pathlib import Path\n"
            "p=Path(sys.argv[-1])\n"
            "sys.exit(0 if 'allowed' in p.read_text() else 1)\n",
            encoding="utf-8",
        )
        self.command = (
            f'"{sys.executable}" "{self.compiler}" {{source}}'
        )

    def test_accepted_inputs_are_stable(self):
        case = self.path / "case.ores"
        case.write_text("allowed  tokens\n", encoding="utf-8")
        result = audit_case(
            case, [sys.executable, str(self.formatter)],
            self.command, self.command, 5,
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["shared_supported"], result)
        self.assertTrue(result["idempotent"], result)

    def test_rejected_both_forms_are_not_counted_as_shared(self):
        case = self.path / "case.ores"
        case.write_text("unsupported", encoding="utf-8")
        result = audit_case(
            case, [sys.executable, str(self.formatter)],
            self.command, self.command, 5,
        )
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["shared_supported"], result)

    def test_compiler_acceptance_change_fails(self):
        case = self.path / "case.ores"
        case.write_text("must  stay", encoding="utf-8")
        discriminator = self.path / "sensitive.py"
        discriminator.write_text(
            "import sys\nfrom pathlib import Path\n"
            "sys.exit(0 if 'must  stay' in Path(sys.argv[-1]).read_text() else 1)\n",
            encoding="utf-8",
        )
        sensitive_command = (
            f'"{sys.executable}" "{discriminator}" {{source}}'
        )
        result = audit_case(
            case, [sys.executable, str(self.formatter)],
            sensitive_command, self.command, 5,
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(
            result["backends"]["java"],
            {"original": "accept", "formatted": "reject"},
        )

    def test_missing_compiler_is_not_success(self):
        case = self.path / "case.ores"
        case.write_text("allowed", encoding="utf-8")
        result = audit_case(
            case, [sys.executable, str(self.formatter)],
            "/unavailable/oreslang-compiler {source}",
            self.command, 5,
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["backends"]["java"]["original"], "unavailable")

    def test_empty_formatter_argv_fails_closed(self):
        case = self.path / "case.ores"
        case.write_text("allowed", encoding="utf-8")
        status, returncode, stdout, stderr = run_process([], 5, stdout_limit=16)
        self.assertEqual(status, "unavailable")
        self.assertIsNone(returncode)
        self.assertEqual(stdout, b"")
        self.assertIn(b"empty process command", stderr)
        result = audit_case(case, [], self.command, self.command, 5)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["formatter"], "unavailable")

    def test_command_template_requires_explicit_source(self):
        with self.assertRaises(ValueError):
            command_argv("compiler --compile", self.path / "input.ores")
        self.assertEqual(
            command_argv('compiler --check "{source}"', self.path / "input.ores"),
            ["compiler", "--check", str(self.path / "input.ores")],
        )

    def test_compiler_rejection_distinct_from_broken_invocation(self):
        expected_codes = ((0, "accept"), (1, "reject"),
                          (2, "usage"), (4, "error"))
        for exit_code, expected_status in expected_codes:
            with self.subTest(exit_code=exit_code):
                argv = [sys.executable, "-c",
                        f"import sys; sys.exit({exit_code})"]
                actual, _ = execute(argv, 5)
                self.assertEqual(actual, expected_status)

    def test_synthetic_reject_script_exits_cleanly(self):
        # Do not let an accidentally invalid mock count as a compiler reject.
        reject = self.path / "clean_reject.py"
        reject.write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
        result, diagnostics = execute([sys.executable, str(reject)], 5)
        self.assertEqual(result, "reject")
        self.assertEqual(diagnostics, b"")

    def test_usage_errors_cannot_fake_successful_rejection(self):
        case = self.path / "usage.ores"
        case.write_text("allowed", encoding="utf-8")
        bad = self.path / "bad_cli.py"
        bad.write_text("import sys\nsys.exit(2)\n", encoding="utf-8")
        broken_command = f'"{sys.executable}" "{bad}" {{source}}'
        result = audit_case(
            case, [sys.executable, str(self.formatter)],
            broken_command, broken_command, 5,
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["backends"]["java"]["original"], "usage")

    def test_cli_fails_closed_when_no_compiler_accepts(self):
        case = self.path / "case.ores"
        case.write_text("unsupported", encoding="utf-8")
        result = main([
            "--formatter", f"{sys.executable} {self.formatter}",
            "--java-command", self.command,
            "--llvm-command", self.command,
            "--cases", str(self.path),
        ])
        self.assertEqual(result, 1)

    def test_cli_fails_closed_on_backend_disagreement(self):
        case = self.path / "case.ores"
        case.write_text("allowed", encoding="utf-8")
        always_reject = self.path / "reject.py"
        always_reject.write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
        result = main([
            "--formatter", f"{sys.executable} {self.formatter}",
            "--java-command", self.command,
            "--llvm-command", f"{sys.executable} {always_reject} {{source}}",
            "--cases", str(self.path),
        ])
        self.assertEqual(result, 1)

    def test_cli_accepts_shared_supported_case(self):
        case = self.path / "case.ores"
        case.write_text("allowed", encoding="utf-8")
        result = main([
            "--formatter", f"{sys.executable} {self.formatter}",
            "--java-command", self.command,
            "--llvm-command", self.command,
            "--cases", str(self.path),
        ])
        self.assertEqual(result, 0)

    def test_rejects_symlink_fixture_without_executing_compilers(self):
        target = self.path / "other.txt"
        target.write_text("allowed", encoding="utf-8")
        case = self.path / "linked.ores"
        case.symlink_to(target)
        result = audit_case(
            case, [sys.executable, str(self.formatter)],
            self.command, self.command, 5,
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["fixture"], "invalid")

    def test_rejects_oversized_fixtures(self):
        case = self.path / "large.ores"
        case.write_text("allowed" * 40, encoding="utf-8")
        result = audit_case(
            case, [sys.executable, str(self.formatter)],
            self.command, self.command, 5, max_case_bytes=32,
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["fixture"], "invalid")

    def test_rejects_unbounded_max_case_bytes(self):
        # Cap before file reads or spawning a formatter/real compiler.
        case = self.path / "case.ores"
        case.write_text("allowed", encoding="utf-8")
        result = audit_case(
            case, [sys.executable, str(self.formatter)],
            self.command, self.command, 5, max_case_bytes=8 * 1024 * 1024 + 1,
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["fixture"], "invalid")

    def test_cli_rejects_unbounded_max_case_bytes(self):
        case = self.path / "case.ores"
        case.write_text("allowed", encoding="utf-8")
        with self.assertRaises(SystemExit) as raised:
            main([
                "--formatter", f"{sys.executable} {self.formatter}",
                "--java-command", self.command,
                "--llvm-command", self.command,
                "--cases", str(self.path),
                "--max-case-bytes", str(8 * 1024 * 1024 + 1),
            ])
        self.assertEqual(raised.exception.code, 2)

    def test_rejects_formatter_erasure(self):
        case = self.path / "case.ores"
        case.write_text("allowed", encoding="utf-8")
        eraser = self.path / "eraser.py"
        eraser.write_text("print('   ')", encoding="utf-8")
        result = audit_case(
            case, [sys.executable, str(eraser)],
            self.command, self.command, 5,
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["formatter"], "invalid_output")

    def test_rejects_invalid_formatter_utf8(self):
        case = self.path / "case.ores"
        case.write_text("allowed", encoding="utf-8")
        malformed = self.path / "malformed.py"
        malformed.write_text(
            "import sys\nsys.stdout.buffer.write(bytes([255]))\n",
            encoding="utf-8",
        )
        result = audit_case(
            case, [sys.executable, str(malformed)],
            self.command, self.command, 5,
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["formatter"], "invalid_utf8")

    def test_rejects_formatter_output_above_limit(self):
        case = self.path / "case.ores"
        case.write_text("allowed", encoding="utf-8")
        expanding = self.path / "expanding.py"
        expanding.write_text("print('x' * 128)", encoding="utf-8")
        result = audit_case(
            case, [sys.executable, str(expanding)],
            self.command, self.command, 5, max_case_bytes=32,
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["formatter"], "invalid_output")

    def test_compiler_standard_output_is_ignored(self):
        result, diagnostics = execute(
            [sys.executable, "-c", "print('x' * 100000); exit(0)"], 5,
        )
        self.assertEqual(result, "accept")
        self.assertEqual(diagnostics, b"")


    def test_invalid_utf8_fixture_fails_before_compiler(self):
        case = self.path / "case.ores"
        case.write_bytes(b"\xff")
        result = audit_case(
            case, [sys.executable, str(self.formatter)],
            self.command, self.command, 5,
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["fixture"], "invalid")

    def test_rejects_fifo_fixture_without_blocking(self):
        import os
        if not hasattr(os, "mkfifo"):
            self.skipTest("POSIX FIFOs required")
        case = self.path / "pipe.ores"
        os.mkfifo(case)
        result = audit_case(
            case, [sys.executable, str(self.formatter)],
            self.command, self.command, 5,
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["fixture"], "invalid")

    def test_formatter_output_flood_is_killed_at_limit(self):
        case = self.path / "case.ores"
        case.write_text("allowed", encoding="utf-8")
        flooder = self.path / "flooder.py"
        flooder.write_text(
            "import sys\nwhile True:\n"
            " sys.stdout.buffer.write(b'x' * 65536)\n"
            " sys.stdout.buffer.flush()\n", encoding="utf-8",
        )
        result = audit_case(
            case, [sys.executable, str(flooder)],
            self.command, self.command, 3, max_case_bytes=32,
        )
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["formatter"], "invalid_output")

    def test_selector_constructor_failure_reaps_child(self):
        # DefaultSelector() may fail after Popen() has already spawned a child.
        with mock.patch("check_compiler_parity.selectors.DefaultSelector",
                        side_effect=OSError("selector setup failed")):
            status, returncode, stdout, diagnostics = run_process(
                [sys.executable, "-c",
                 "import time; time.sleep(10)"],
                2, stdout_limit=64,
            )
        self.assertEqual(status, "unavailable")
        self.assertIsNotNone(returncode)
        self.assertEqual(stdout, b"")
        self.assertIn(b"selector setup failed", diagnostics)

    def test_selector_value_error_fails_closed(self):
        with mock.patch("check_compiler_parity.selectors.DefaultSelector",
                        side_effect=ValueError("invalid selector")):
            status, returncode, stdout, diagnostics = run_process(
                [sys.executable, "-c",
                 "import time; time.sleep(10)"],
                2, stdout_limit=64,
            )
        self.assertEqual(status, "unavailable")
        self.assertIsNotNone(returncode)
        self.assertEqual(stdout, b"")
        self.assertIn(b"invalid selector", diagnostics)

    def test_compiler_stderr_flood_is_bounded(self):
        status, diagnostics = execute(
            [sys.executable, "-c",
             "import sys; sys.stderr.write('x' * 1000000)"], 5,
        )
        self.assertEqual(status, "accept")
        self.assertEqual(len(diagnostics), 4000)

    def test_compiler_timeout_is_not_rejection(self):
        status, _ = execute(
            [sys.executable, "-c", "import time; time.sleep(5)"], 0.2,
        )
        self.assertEqual(status, "timeout")

    def test_second_pass_expansion_fails_idempotence(self):
        case = self.path / "case.ores"
        case.write_text("allowed", encoding="utf-8")
        second = self.path / "second.py"
        second.write_text(
            "import sys\n"
            "print('x' * 200 if 'formatted' in sys.argv[-1] else 'allowed')\n",
            encoding="utf-8",
        )
        result = audit_case(
            case, [sys.executable, str(second)],
            self.command, self.command, 5, max_case_bytes=32,
        )
        self.assertFalse(result["ok"], result)
        self.assertFalse(result["idempotent"])


if __name__ == "__main__":
    unittest.main()
