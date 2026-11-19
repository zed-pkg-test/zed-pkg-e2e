"""Adversarial/seeded tests for deliberately restricted static actor AOT."""
import hashlib
import importlib.util
from pathlib import Path
import re
import random
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "tools/atto_static_actor_aot.py"
SPEC = importlib.util.spec_from_file_location("atto_static_actor_aot_hardened", FILE)
atto = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(atto)
ORIGINAL = (ROOT / "fixtures/m4/shared_actor_counter.atto").read_text()
ACTOR = ORIGINAL.split("pub fnc main()", 1)[0]

def program(actors=2, messages=(("a0", 7), ("a1", 9), ("a0", 8)),
            actor_type="Counter", main_return=43):
    return (ACTOR +
            "\npub fnc main(): int {\n" +
            "".join("  const a%d = spawn %s();\n" % (i, actor_type) for i in range(actors)) +
            "".join("  %s.send(%s);\n" % (actor, value) for actor, value in messages) +
            "".join("  stop a%d;\n" % i for i in range(actors)) +
            "  return %d;\n}\n" % main_return)

class HardenedStaticCompilerTests(unittest.TestCase):
    def compile(self, source):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            src = root / "fixture.atto"
            src.write_text(source)
            out = root / "compiled"
            p = subprocess.run(
                [sys.executable, str(FILE), str(src), "--source-sha256",
                 hashlib.sha256(src.read_bytes()).hexdigest(), "--out", str(out)],
                capture_output=True, text=True, timeout=12)
            return p, out.exists()

    def rejects(self, source, marker):
        p, produced = self.compile(source)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn(marker, p.stderr)
        self.assertFalse(produced, "rejected input published an artifact")

    def test_source_identical_to_m4_contract(self):
        self.assertIn("define actor Counter", ORIGINAL)
        kinds, actions, result, sends = atto.parse_atto(ORIGINAL)
        self.assertEqual((len(kinds), result, sends), (1, 43, 3))
        self.assertEqual(sum(op[0] == "spawn" for op in actions), 2)

    def test_state_digest_mutation_is_preflighted_before_commit(self):
        java = atto.generate(*atto.parse_atto(ORIGINAL)[:3])
        actor = java.split("long send(long value) {", 1)[1].split("return next;", 1)[0]
        self.assertLess(actor.index("Math.multiplyExact"), actor.index("state.set("))
        self.assertLess(actor.index("Math.addExact(sentMessages"), actor.index("state.set("))
        self.assertLess(actor.index("state.set("), actor.index("sentMessages = nextCount;"))
        self.assertNotIn("sentMessages++", actor)

    def test_64_native_actor_instance_budget_allowed(self):
        p, output = self.compile(program(actors=64, messages=(("a0", 1),)))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(output)

    def test_65th_actor_instance_rejected(self):
        self.rejects(program(actors=65, messages=(("a0", 1),)),
                     "actor instance budget exceeded")

    def test_message_count_budget_enforced_by_parser(self):
        source = program(actors=1, messages=[("a0", 1)] * (atto.MAX_SENDS + 1))
        with self.assertRaisesRegex(atto.CompileError, "actor message budget exceeded"):
            atto.parse_atto(source)

    def test_exact_message_budget_parser_boundary(self):
        source = program(actors=1, messages=[("a0", 1)] * atto.MAX_SENDS)
        kinds, actions, _, sends = atto.parse_atto(source)
        self.assertEqual(sends, atto.MAX_SENDS)
        self.assertEqual(sum(op[0] == "stop" for op in actions), 1)

    def test_unicode_identifier_spoofing_deny(self):
        self.rejects(ORIGINAL.replace("increment", "incrеment"), "unsupported AttoLang token")

    def test_block_comment_not_silently_ignored(self):
        self.rejects(ORIGINAL.replace("stop first;", "/* hidden */ stop first;"),
                     "unsupported AttoLang token")

    def test_forbid_unimplemented_ownership_semantics(self):
        self.rejects(ORIGINAL.replace("first.send(7);", "rt take first; first.send(7);"),
                     "expected")

    def test_forbid_direct_state_access(self):
        self.rejects(ORIGINAL.replace("second.send(9);", "second.total = 9;"), "expected")

    def test_forbid_sneaking_java_code(self):
        self.rejects(ORIGINAL + "\nimport java.lang.Runtime;\n", "unexpected code after main")

    def test_check_true_signed_i64_edges_and_refuse_overflow(self):
        for v in (str(-(1 << 63)), str((1 << 63)-1)):
            p, out = self.compile(ORIGINAL.replace("first.send(7);", f"first.send({v});"))
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertTrue(out)
        for v in (str(1 << 63), str(-(1 << 63)-1)):
            self.rejects(ORIGINAL.replace("first.send(7);", f"first.send({v});"),
                         "exceeds the AttoLang native i64 range")

    def test_untouched_randomized_sequential_actor_traces(self):
        rng = random.Random(20261107)
        for case in range(96):
            actors = rng.randint(1, 12)
            messages = [(f"a{rng.randrange(actors)}", rng.randint(-9, 9))
                        for _ in range(rng.randint(1, 20))]
            source = program(actors=actors, messages=messages, main_return=43)
            kinds, actions, result, sends = atto.parse_atto(source)
            generated = atto.generate(kinds, actions, result)
            self.assertEqual(result, 43, case)
            self.assertEqual(sends, len(messages), case)
            self.assertEqual(generated.count(".send("), sends, case)
            self.assertEqual(len(re.findall(r"handle_\d+\.close\(\);", generated)), actors, case)
            actual = [(a, value) for op, a, value in actions if op == "send"]
            self.assertEqual(actual, messages, case)
            self.assertIn("Arena.ofConfined()", generated, case)
            self.assertNotIn("ScriptEngineManager", generated, case)

    def test_reject_invalid_tail_syntax_without_fallback(self):
        for extra in ("invoke reflection;", "gpu {}", "rt borrow mut a0;",
                      "throw Error();", "await spawn Counter();"):
            mutated = ORIGINAL.replace("return 43;", extra + "\n  return 43;")
            with self.subTest(extra=extra):
                p, produced = self.compile(mutated)
                self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
                self.assertFalse(produced)

if __name__ == "__main__":
    unittest.main()
