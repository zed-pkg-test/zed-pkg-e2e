"""Typed actor IR shape checks only. The actual trusted exporter is built by LLVM CI."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("m5_static_actor_codegen", ROOT / "tools/atto_static_actor_aot.py")
compiler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compiler)

def fixture_ir():
    return {
        "schema":"atto.canonical-checked-actor-ir.v0",
        "checker":"LLVM_PARSE_PROGRAM_OWNERSHIP_SCALAR_TYPES",
        "status":"CHECKED_RESTRICTED_ACTOR_SLICE",
        "actor":{"name":"Counter","field":"total","initial":0,
                 "parameter":"increment","operation":"checked_add_i64_return_field"},
        "spawns":[{"ref":"first","kind":"Counter"},{"ref":"second","kind":"Counter"}],
        "actions":[{"op":"send","ref":"first","value":7},
                   {"op":"send","ref":"second","value":9},
                   {"op":"send","ref":"first","value":8},
                   {"op":"stop","ref":"first"},{"op":"stop","ref":"second"}],
        "return_i64":43,
        "limits":{"max_actor_instances":64,"max_source_sends":1024},
        "unsupported":["await","critical-read","critical-write","rt_take","rt_share",
                       "dynamic_guest_code","tenant_sandbox"],
    }

class M5CanonicalIrChecks(unittest.TestCase):
    def test_validated_shape_lowers_to_identical_static_java(self):
        kinds, actions, result, sends = compiler.validate_checked_actor_ir(fixture_ir())
        src = (ROOT / "fixtures/m4/shared_actor_counter.atto").read_text()
        old_kinds, old_actions, old_result, old_sends = compiler.parse_atto(src)
        self.assertEqual((kinds, actions, result, sends),
                         (old_kinds, old_actions, old_result, old_sends))
        self.assertEqual(compiler.generate(kinds, actions, result),
                         compiler.generate(old_kinds, old_actions, old_result))

    def denied(self, mutation):
        ir = copy.deepcopy(fixture_ir())
        mutation(ir)
        with self.assertRaises((compiler.CompileError, TypeError, ValueError)):
            compiler.validate_checked_actor_ir(ir)

    def test_reject_unknown_schema(self):
        self.denied(lambda x: x.update(schema="untyped.v0"))

    def test_reject_forged_checker(self):
        self.denied(lambda x: x.update(checker="PYTHON_OWNERSHIP_CHECKER"))

    def test_reject_missing_ownership_checker_marker(self):
        self.denied(lambda x: x.update(status="PENDING"))

    def test_reject_unknown_field(self):
        self.denied(lambda x: x.update(fake_capability=True))

    def test_reject_classic_direct_field_escaping(self):
        self.denied(lambda x: x["actor"].update(operation="unsealed_direct_field_write"))

    def test_reject_heap_owner_invalid_kind(self):
        self.denied(lambda x: x["actor"].update(initial=2**64))

    def test_reject_duplicated_handle(self):
        self.denied(lambda x: x["spawns"][1].update(ref="first"))

    def test_reject_wrong_actor_type(self):
        self.denied(lambda x: x["spawns"][1].update(kind="OtherActor"))

    def test_reject_send_after_stop(self):
        self.denied(lambda x: x["actions"].append({"op":"send","ref":"first","value":1}))

    def test_reject_unstopped_actor(self):
        self.denied(lambda x: x["actions"].pop())

    def test_reject_non_i64_message(self):
        self.denied(lambda x: x["actions"][0].update(value="7"))

    def test_reject_boolean_i64_payload(self):
        self.denied(lambda x: x["actions"][0].update(value=True))

    def test_reject_overlarge_send_budget(self):
        self.denied(lambda x: x["actions"].__setitem__(slice(None), [
            {"op":"send","ref":"first","value":1} for _ in range(1025)
        ] + [{"op":"stop","ref":"first"},{"op":"stop","ref":"second"}]))

    def test_reject_unapproved_ownership_feature(self):
        self.denied(lambda x: x.update(unsupported=["await"]))

    def test_reject_non_i64_return(self):
        self.denied(lambda x: x.update(return_i64=2**64))

    def test_reject_unknown_action(self):
        self.denied(lambda x: x["actions"][0].update(op="borrow_mut"))

    def test_reject_reused_handle_after_stop(self):
        self.denied(lambda x: x["actions"].insert(4, {"op":"send","ref":"first","value":1}))

if __name__ == "__main__":
    unittest.main()
