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



    def test_guard_false_native_main_returns_early_after_all_actors_stop(self):
        self.source.write_text(self.source.read_text().replace(
            "return 43;", "guard false { return 7; }\n  return 43;"))
        result = self.compile()
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(manifest["expected_guest_result"], 7)
        java = (self.output / "AttoGeneratedMain.java").read_text()
        self.assertIn("if (!false) return 7L;", java)
        self.assertLess(java.index("handle_1.close();"), java.index("if (!false) return 7L;"))
        self.assertIn("return 43L;", java)

    def test_guard_true_native_main_falls_through(self):
        self.source.write_text(self.source.read_text().replace(
            "return 43;", "guard true { return 7; }\n  return 43;"))
        result = self.compile()
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(manifest["expected_guest_result"], 43)
        self.assertIn("if (!true) return 7L;",
                      (self.output / "AttoGeneratedMain.java").read_text())

    def test_first_false_guard_wins_without_skipping_actor_teardown(self):
        self.source.write_text(self.source.read_text().replace(
            "return 43;", "guard true { return 8; }\n"
                          "  guard false { return 7; }\n"
                          "  guard false { return 9; }\n  return 43;"))
        result = self.compile()
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(manifest["expected_guest_result"], 7)

    def test_guard_cannot_escape_before_actor_retirement(self):
        self.rejects("guard may exit only after all actor handles are stopped",
                     self.source.read_text().replace(
                         "stop first;", "guard true { return 7; }\n  stop first;"))

    def test_guard_rejects_int_truthiness(self):
        self.rejects("guard condition must be a bool literal",
                     self.source.read_text().replace(
                         "return 43;", "guard 1 { return 7; }\n  return 43;"))

    def test_guard_rejects_unknown_boolean_variable_without_typed_scope(self):
        self.rejects("guard condition must be a bool literal",
                     self.source.read_text().replace(
                         "return 43;", "guard ready { return 7; }\n  return 43;"))

    def test_guard_requires_terminal_return(self):
        self.rejects("expected 'return'",
                     self.source.read_text().replace(
                         "return 43;", "guard false { 7; }\n  return 43;"))

    def test_guard_rejects_redundant_else(self):
        original = self.source.read_text()
        for marker in ("return", "^"):
            self.rejects("E_GUARD_ELSE_NOT_REQUIRED",
                         original.replace(
                             "return 43;",
                             "guard false { " + marker + " 7; } else { " +
                             marker + " 8; }\n  return 43;"))

    def test_explicit_suspend_lambda_capture_is_not_fake_compiled(self):
        self.rejects("unsupported AttoLang token",
                     self.source.read_text().replace(
                         "return 43;",
                         "val pending = task { ^ 41; }; " +
                         "^ pending |> susp |f| -> await f >> val answer: int;"))

    def test_unlowered_await_is_rejected_before_native_source(self):
        self.rejects("expected a signed integer literal",
                     self.source.read_text().replace(
                         "return 43;", "^ await pending;"))

    def test_typed_channel_future_profile_is_rejected_before_aot(self):
        self.rejects("unsupported AttoLang token",
                     self.source.read_text().replace(
                         "return 43;",
                         "val result: Future<Result<Option<int>,IoError>> = " +
                         "nb readch channel; ^ 42;"))

    def test_guard_rejects_extra_effect_after_early_return(self):
        self.rejects("expected '}'",
                     self.source.read_text().replace(
                         "return 43;", "guard false { return 7; stop first; }\n  return 43;"))

    def test_guard_rejects_i64_overflow_before_native_artifact(self):
        self.rejects("exceeds the AttoLang native i64 range",
                     self.source.read_text().replace(
                         "return 43;", "guard false { return 9223372036854775808; }\n  return 43;"))

    def test_guard_keyword_is_reserved_in_actor_name(self):
        self.rejects("reserved AttoLang guard keyword",
                     self.source.read_text().replace("Counter", "guard"))

    def test_guard_keyword_is_reserved_as_actor_handle(self):
        self.rejects("reserved AttoLang guard keyword",
                     self.source.read_text().replace("const first", "const guard"))

    def test_guard_limit_is_eight(self):
        cases = "guard true { return 1; }\n  " * 9
        self.rejects("guard v1 limit exceeded (8)",
                     self.source.read_text().replace("return 43;", cases + "return 43;"))



    def test_compiler_generated_closed_world_actor_graph_is_bounded_and_explicit(self):
        result = self.compile()
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.output / "manifest.json").read_text())
        cw = manifest["closed_world"]
        self.assertEqual(cw["schema"], "atto.static-actor.closed-world.pilot.v1")
        self.assertEqual(cw["status"], "COMPILER_GENERATED_RESTRICTED_GRAPH_NOT_CERTIFIED")
        self.assertEqual(cw["single_source_sha256"], manifest["source_sha256"])
        self.assertEqual(cw["entry_roots"], ["AttoGeneratedMain.guest_main"])
        self.assertEqual(cw["declared_actor_nodes"], [
            {"source_actor": "Counter", "generated_type": "AttoGeneratedMain.Actor_0"}])
        self.assertEqual(cw["actor_roots"], [{
            "source_actor": "Counter",
            "generated_type": "AttoGeneratedMain.Actor_0",
            "handler": "AttoGeneratedMain.Actor_0.send",
            "retirement": "AttoGeneratedMain.Actor_0.close",
        }])
        self.assertEqual(cw["actor_instance_spawns"], 2)
        self.assertEqual(cw["action_count"], 7)
        self.assertEqual(cw["action_kinds"], ["send", "spawn", "stop"])
        self.assertEqual(cw["generic_specializations"], [])
        self.assertEqual(cw["typeclass_witnesses"], [])
        self.assertEqual(cw["guest_dynamic_imports"], [])
        self.assertEqual(cw["host_primitives"], [
            "ffm.confined_actor_arena", "java.checked_arithmetic",
            "java.gc_metrics", "java.stdout_observation",
        ])
        for name in ("guest_dynamic_loading", "guest_runtime_reflection",
                     "guest_runtime_codegen", "native_aot_artifact_verified",
                     "authenticated_execution_receipt"):
            self.assertIs(cw[name], False, name)
        java = (self.output / "AttoGeneratedMain.java").read_text()
        self.assertIn("private static final class Actor_0", java)
        self.assertIn("private static long guest_main()", java)
        self.assertNotIn("Class.forName(", java)
        self.assertNotIn("Proxy.newProxyInstance(", java)

    def test_actor_reachability_excludes_unspawned_declared_actor(self):
        original = self.source.read_text()
        actor = original.split("pub fnc main(): int {")[0]
        extra = actor.replace("Counter", "Idle")
        self.source.write_text(original.replace(
            "pub fnc main(): int {", extra + "pub fnc main(): int {"))
        result = self.compile()
        self.assertEqual(result.returncode, 0, result.stderr)
        cw = json.loads((self.output / "manifest.json").read_text())["closed_world"]
        self.assertEqual([x["source_actor"] for x in cw["declared_actor_nodes"]],
                         ["Counter", "Idle"])
        self.assertEqual([x["source_actor"] for x in cw["actor_roots"]], ["Counter"])
        self.assertEqual(cw["actor_instance_spawns"], 2)

    def test_guards_are_counted_as_static_graph_actions_not_dynamic_imports(self):
        self.source.write_text(self.source.read_text().replace(
            "return 43;", "guard false { return 7; }\n  return 43;"))
        result = self.compile()
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.output / "manifest.json").read_text())
        cw = manifest["closed_world"]
        self.assertEqual(manifest["expected_guest_result"], 7)
        self.assertEqual(cw["action_kinds"], ["guard", "send", "spawn", "stop"])
        self.assertEqual(cw["action_count"], 8)
        self.assertEqual(cw["guest_dynamic_imports"], [])
        self.assertIs(cw["authenticated_execution_receipt"], False)

    def test_manifest_is_deterministic_over_identical_admitted_source(self):
        first = self.compile()
        self.assertEqual(first.returncode, 0, first.stderr)
        before = (self.output / "manifest.json").read_bytes()
        second_out = self.dir / "second"
        second = self.compile(out=second_out)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(before, (second_out / "manifest.json").read_bytes())

    def test_forbidden_dynamic_guest_source_has_no_closed_world_manifest(self):
        self.rejects("expected", self.source.read_text().replace(
            "return 43;", "return Class.forName(7);"))



    def test_caret_main_return_matches_keyword_after_actor_teardown(self):
        self.source.write_text(self.source.read_text().replace("return 43;", "^ 43;"))
        result = self.compile()
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(manifest["expected_guest_result"], 43)
        self.assertIn("return 43L;", (self.output / "AttoGeneratedMain.java").read_text())

    def test_caret_guard_fail_arm_and_final_return(self):
        self.source.write_text(self.source.read_text().replace(
            "return 43;", "guard false { ^ 7; }\n  ^ 43;"))
        result = self.compile()
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(manifest["expected_guest_result"], 7)
        java = (self.output / "AttoGeneratedMain.java").read_text()
        self.assertIn("if (!false) return 7L;", java)
        self.assertLess(java.index("handle_1.close();"), java.index("if (!false) return 7L;"))

    def test_caret_actor_receive_return_is_lexical(self):
        self.source.write_text(self.source.read_text().replace("return total;", "^ total;"))
        result = self.compile()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.output / "manifest.json").read_text())["expected_guest_result"], 43)

    def test_caret_guard_cannot_bypass_actor_retirement(self):
        self.rejects("guard may exit only after all actor handles are stopped",
                     self.source.read_text().replace(
                         "stop first;", "guard false { ^ 7; }\n  stop first;"))

    def test_infix_caret_is_not_xor_and_emits_no_artifact(self):
        self.rejects("expected ';'", self.source.read_text().replace(
            "return 43;", "return 4 ^ 3;"))

    def test_caret_cannot_appear_in_value_position(self):
        self.rejects("expected a signed integer literal", self.source.read_text().replace(
            "return 43;", "return ^ 43;"))

    def test_double_caret_has_no_lexical_return_meaning(self):
        self.rejects("expected a signed integer literal", self.source.read_text().replace(
            "return 43;", "^ ^ 43;"))



    def test_caret_static_native_image_fixture_is_compilable(self):
        caret_fixture = ROOT / "fixtures/actor_counter_caret.atto"
        self.source.write_bytes(caret_fixture.read_bytes())
        result = self.compile()
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(manifest["expected_guest_result"], 7)
        self.assertEqual(manifest["actor_sends"], 3)

if __name__ == "__main__":
    unittest.main()
