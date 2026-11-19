"""M7 closed-world compiler checks: malformed/unsafe input never generates Java."""
import hashlib
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPILER = ROOT / "tools/m7_compile_actor_continuation.py"
SPEC = importlib.util.spec_from_file_location("m7_coop_compiler", COMPILER)
compiler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compiler)
FIXTURE = (ROOT / "fixtures/m7/cooperative_sum.atto").read_text()

class M7RestrictedCooperateCompilerTest(unittest.TestCase):
    def test_real_atto_source_lowers_to_saved_pc_and_checked_arithmetic(self):
        actor, digest, java = compiler.compile_source(FIXTURE)
        self.assertEqual(actor["name"], "CooperativeSum")
        self.assertEqual(digest, hashlib.sha256(FIXTURE.encode()).hexdigest())
        self.assertIn("public final class M7GeneratedActor", java)
        self.assertIn("case 2:", java)
        self.assertIn("pc = 1; // Commit next PC BEFORE yielding", java)
        self.assertIn("Math.addExact(total, index)", java)
        self.assertIn("slice.cooperate()", java)
        self.assertIn("Step.YIELD", java)
        self.assertNotIn("eval(", java)

    def reject(self, candidate):
        with self.assertRaises(compiler.CompileError):
            compiler.compile_source(candidate)

    def test_missing_cooperation_rejected(self):
        self.reject(FIXTURE.replace("rt cooperate;", ""))
    def test_unsafe_borrow_across_safepoint_rejected(self):
        self.reject(FIXTURE.replace("rt cooperate;", "rt borrow total; rt cooperate;"))
        self.reject(FIXTURE.replace("rt cooperate;", "critical-write total; rt cooperate;"))
    def test_unrecognized_await_and_select_rejected(self):
        self.reject(FIXTURE.replace("rt cooperate;", "await producer; rt cooperate;"))
        self.reject(FIXTURE.replace("rt cooperate;", "select { } rt cooperate;"))
    def test_wrong_increment_and_broken_pc_shape_rejected(self):
        self.reject(FIXTURE.replace("i = i + 1;", "i = i + 2;"))
        self.reject(FIXTURE.replace("total = total + i;", "total = total + i + i;"))
    def test_aliasing_live_slots_rejected(self):
        self.reject(FIXTURE.replace("let i: int = 0;", "let total: int = 0;"))
    def test_unknown_source_prefix_and_code_injection_rejected(self):
        self.reject("import java.lang.System;\n" + FIXTURE)
        self.reject(FIXTURE + " public class Escape {}")
        self.reject(FIXTURE.replace("CooperativeSum", "CooperativeSum$Injected"))
    def test_source_bound_rejected(self):
        self.reject(FIXTURE + " " * 9000)
    def test_invalid_i64_literal_rejected(self):
        self.reject(FIXTURE.replace("let total: int = 0;", "let total: int = 9223372036854775808;"))
        self.reject(FIXTURE.replace("let total: int = 0;", "let total: int = -9223372036854775809;"))

if __name__ == "__main__":
    unittest.main()
