import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPILER = ROOT / "tools/atto_static_actor_aot.py"
FIXTURE = ROOT / "fixtures/actor_counter.atto"


class StaticActorPilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.source = self.dir / "program.atto"
        self.source.write_bytes(FIXTURE.read_bytes())
        self.output = self.dir / "output"

    def compile(self, digest=None, source=None, out=None):
        path = source or self.source
        return subprocess.run(
            [sys.executable, str(COMPILER), str(path), "--source-sha256",
             digest or hashlib.sha256(path.read_bytes()).hexdigest(),
             "--out", str(out or self.output)],
            text=True, capture_output=True, timeout=15)

    def rejects(self, reason, program=None):
        if program is not None:
            self.source.write_text(program)
        r = self.compile()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("ATTO_AOT_COMPILE_REFUSED:", r.stderr)
        self.assertIn(reason, r.stderr)
        self.assertFalse(self.output.exists(), "rejected code published a binary/source artifact")

    def test_genuine_atto_actor_program_is_statically_generated(self):
        r = self.compile()
        self.assertEqual(r.returncode, 0, r.stderr)
        source = (self.output / "AttoGeneratedMain.java").read_text()
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(manifest["actor_kinds"], 1)
        self.assertEqual(manifest["actor_sends"], 3)
        self.assertEqual(manifest["expected_guest_result"], 43)
        self.assertEqual(manifest["status"], "GENERATED_SOURCE_NOT_YET_EXECUTED")
        self.assertIn("Arena.ofConfined()", source)
        self.assertIn("arena.close()", source)
        self.assertEqual(source.count(".send("), 3)
        self.assertIn("Math.addExact", source)
        self.assertNotIn("eval(", source)
        self.assertNotIn("getResourceAsStream", source)
        self.assertEqual(hashlib.sha256(source.encode()).hexdigest(), manifest["generated_java_sha256"])

    def test_changed_message_changes_generated_artifact(self):
        before = self.compile()
        self.assertEqual(before.returncode, 0)
        old = (self.output / "AttoGeneratedMain.java").read_bytes()
        self.output = self.dir / "second"
        self.source.write_text(self.source.read_text().replace("first.send(8);", "first.send(10);"))
        after = self.compile()
        self.assertEqual(after.returncode, 0)
        self.assertNotEqual(old, (self.output / "AttoGeneratedMain.java").read_bytes())

    def test_wrong_source_sha_rejected(self):
        result = self.compile(digest="0"*64)
        self.assertEqual(result.returncode, 2)
        self.assertIn("SHA-256", result.stderr)
        self.assertFalse(self.output.exists())

    def test_source_without_actor_refused(self):
        self.rejects("at least one concrete actor required",
                     "pub fnc main(): int { return 1; }")

    def test_unsupported_actor_return_expression_refused(self):
        self.rejects("expected", self.source.read_text().replace(
            "return total;", "return increment;"))

    def test_direct_actor_state_escape_refused(self):
        self.rejects("expected 'send'", self.source.read_text().replace(
            "first.send(7);", "first.total = 1;"))

    def test_use_after_stop_refused(self):
        self.rejects("message send to nonexistent or retired actor",
                     self.source.read_text().replace("stop first;", "stop first;\n  first.send(7);"))

    def test_double_stop_refused(self):
        self.rejects("invalid or already retired actor handle",
                     self.source.read_text().replace("stop first;", "stop first;\n  stop first;"))

    def test_actor_leak_refused(self):
        self.rejects("actor handle not stopped before return",
                     self.source.read_text().replace("stop second;", ""))

    def test_unknown_actor_type_refused(self):
        self.rejects("unknown actor type",
                     self.source.read_text().replace("spawn Counter()", "spawn Missing()"))

    def test_duplicate_actor_handle_refused(self):
        self.rejects("actor handle redeclared",
                     self.source.read_text().replace("const second = spawn Counter();",
                       "const first = spawn Counter();"))

    def test_unknown_language_construct_refused(self):
        self.rejects("unsupported AttoLang token",
                     self.source.read_text().replace("return 43;", "return 4 * 3;"))

    def test_integer_out_of_i64_range_refused(self):
        self.rejects("exceeds the AttoLang native i64 range",
                     self.source.read_text().replace("first.send(7);",
                       "first.send(9223372036854775808);"))

    def test_symlinked_atto_source_refused(self):
        alias = self.dir / "link.atto"
        alias.symlink_to(self.source)
        result = self.compile(source=alias)
        self.assertEqual(result.returncode, 2)
        self.assertIn("nonsymlinked", result.stderr)

    def test_output_never_overwritten(self):
        self.output.mkdir()
        result = self.compile()
        self.assertEqual(result.returncode, 2)
        self.assertIn("output path exists", result.stderr)

    def test_multibyte_spoofed_identifier_refused(self):
        self.rejects("unsupported AttoLang token",
                     self.source.read_text().replace("Counter", "Cоunter"))

    def test_source_budget_rejected(self):
        self.source.write_bytes(b"/" * 33000)
        result = self.compile()
        self.assertEqual(result.returncode, 2)
        self.assertIn("source byte budget", result.stderr)


if __name__ == "__main__":
    unittest.main()
