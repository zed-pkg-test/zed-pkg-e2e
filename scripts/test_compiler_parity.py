"""Offline harness self-tests; these are NOT Java/LLVM compiler parity evidence."""
import sys
import tempfile
import unittest
from pathlib import Path

from check_compiler_parity import audit_case, command_argv, execute, main


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


if __name__ == "__main__":
    unittest.main()
