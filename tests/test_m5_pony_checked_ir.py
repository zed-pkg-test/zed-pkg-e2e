"""Pony-inspired fail-closed checks on the real typed IR GraalVM ingress.

These tests do NOT establish Pony-style graph ownership in the compiler.
They prevent this intentionally restricted backend from silently accepting
unproved aliasing / capability or malformed checked-IR inputs.
"""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "m5_existing_checked_ir_tests", ROOT / "tests/test_m5_checked_actor_ir.py")
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)
compiler = old.compiler

class PonyCheckedActorIRTests(unittest.TestCase):
    def fixture(self):
        return old.fixture_ir()

    def reject(self, patch):
        doc=copy.deepcopy(self.fixture())
        patch(doc)
        with self.assertRaises(compiler.CompileError):
            compiler.validate_checked_actor_ir(doc)

    def test_existing_m4_checked_ir_still_lowers_to_same_native_source(self):
        doc=self.fixture()
        kinds,actions,result,sends=compiler.validate_checked_actor_ir(doc)
        self.assertEqual(sends,3)
        self.assertEqual(result,43)
        self.assertEqual(len([a for a in actions if a[0]=="stop"]),2)
        emitted=compiler.generate(kinds,actions,result)
        self.assertIn("Math.addExact",emitted)

    def test_missing_critical_state_exclusion_refused(self):
        self.reject(lambda d: d["unsupported"].remove("critical-write"))

    def test_missing_take_exclusion_refused(self):
        self.reject(lambda d: d["unsupported"].remove("rt_take"))

    def test_missing_share_exclusion_refused(self):
        self.reject(lambda d: d["unsupported"].remove("rt_share"))

    def test_unverified_pony_capability_claim_refused(self):
        self.reject(lambda d: d["unsupported"].append("Pony_iso_transitive_graph_proved"))

    def test_unverified_unsafe_pointers_refused(self):
        self.reject(lambda d: d["actor"].update(pointer_to_mutable_graph=42))

    def test_unverified_deep_freeze_action_refused(self):
        self.reject(lambda d: d["actions"].insert(
            0,{"op":"deep_freeze","ref":"first","value":0}))

    def test_tag_handle_does_not_authorize_direct_field_access(self):
        self.reject(lambda d: d["actions"][0].update(
            op="tag", read_field="total"))

    def test_type_confused_unhashable_handle_refused_cleanly(self):
        self.reject(lambda d: d["actions"][0].update(ref={"actor":"first"}))

    def test_type_confused_list_ref_refused_cleanly(self):
        self.reject(lambda d: d["actions"][0].update(ref=["first"]))

    def test_boolean_payload_not_treated_as_checked_i64(self):
        self.reject(lambda d: d["actions"][0].update(value=True))

    def test_undocumented_capability_substitution_refused(self):
        self.reject(lambda d: d["actor"].update(operation="rt_borrow_mut"))

    def test_unchecked_forged_canonical_status_refused(self):
        self.reject(lambda d: d.update(status="FULL_PONY_REFERENCE_CHECKER_CERTIFIED"))

    def test_valid_unique_json_decodes_exactly(self):
        raw=json.dumps(self.fixture(),sort_keys=True).encode()
        self.assertEqual(compiler.decode_checked_ir(raw),self.fixture())

    def test_duplicate_top_level_json_key_rejected(self):
        with self.assertRaises(compiler.CompileError):
            compiler.decode_checked_ir(b'{"schema":"a","schema":"b"}')

    def test_duplicate_nested_json_key_rejected(self):
        with self.assertRaises(compiler.CompileError):
            compiler.decode_checked_ir(b'{"actor":{"name":"Counter","name":"Forged"}}')

    def test_nonfinite_json_nan_is_denied(self):
        with self.assertRaises(compiler.CompileError):
            compiler.decode_checked_ir(b'{"actor":{"initial":NaN}}')

    def test_nonfinite_json_infinity_is_denied(self):
        with self.assertRaises(compiler.CompileError):
            compiler.decode_checked_ir(b'{"actor":{"initial":Infinity}}')

    def test_invalid_utf8_denied_before_type_projection(self):
        with self.assertRaises(compiler.CompileError):
            compiler.decode_checked_ir(b'{"actor":"\xff"}')

    def test_unknown_binary_pin_fails_before_exec(self):
        with tempfile.TemporaryDirectory() as d:
            executable=Path(d)/"not-a-proven-compiler"
            executable.write_bytes(b"#!/bin/sh\necho faked\n")
            executable.chmod(0o700)
            with self.assertRaisesRegex(compiler.CompileError,
                                        "canonical exporter executable digest mismatch"):
                compiler.compile_with_checked_exporter(b"hello", str(executable),
                                                     "0"*64)

if __name__ == "__main__":
    unittest.main()
