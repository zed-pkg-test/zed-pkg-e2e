"""M8 restricted Java safe-point typed IR receipt tests; not canonical Java compiler."""
import hashlib, importlib.util, json, pathlib, subprocess, sys, tempfile, unittest
ROOT=pathlib.Path(__file__).resolve().parents[2]
COMPILER=ROOT/"tools/m7_compile_actor_continuation.py"
SPEC=importlib.util.spec_from_file_location("m8java",COMPILER)
compiler=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(compiler)
SOURCE=(ROOT/"fixtures/m7/cooperative_sum.atto").read_text()
class IRTests(unittest.TestCase):
    def setUp(self):
        self.parsed,self.sha,self.java=compiler.compile_source(SOURCE)
        self.ir=compiler.make_m8_ir(self.parsed)
    def test_typed_plan(self):
        compiler.validate_m8_ir(self.ir)
        self.assertEqual(self.ir["schema"],"atto.m8.cooperative-ir.v1")
        self.assertEqual([x["pc"] for x in self.ir["instructions"]],[0,1,2,3,4])
        self.assertEqual(self.ir["instructions"][3]["resume_pc"],1)
        self.assertEqual([x["type"] for x in self.ir["live_slots"]],["owned:i64","owned:i64"])
        self.assertIn("slice.cooperate()",self.java)
    def test_reject_budget_tampering(self):
        self.ir["budget_reductions"]["max"]=65537
        with self.assertRaises(compiler.CompileError): compiler.validate_m8_ir(self.ir)
    def test_reject_live_borrow_and_critical_loans(self):
        for key in ("borrow_across_suspend","critical_loan_across_suspend"):
            self.ir=compiler.make_m8_ir(self.parsed)
            self.ir[key]=True
            with self.assertRaises(compiler.CompileError): compiler.validate_m8_ir(self.ir)
    def test_reject_pc_and_slot_alias(self):
        self.ir["instructions"][3]["resume_pc"]=2
        with self.assertRaises(compiler.CompileError): compiler.validate_m8_ir(self.ir)
        self.ir=compiler.make_m8_ir(self.parsed)
        self.ir["live_slots"][1]["name"]=self.ir["live_slots"][0]["name"]
        with self.assertRaises(compiler.CompileError): compiler.validate_m8_ir(self.ir)
    def test_source_effects_fail_closed(self):
        for stmt in ("rt borrow total; rt cooperate;",
                     "critical-read total; rt cooperate;",
                     "await something; rt cooperate;"):
            with self.assertRaises(compiler.CompileError):
                compiler.compile_source(SOURCE.replace("rt cooperate;",stmt))
    def test_manifest_binds_source_ir_and_generated_java(self):
        with tempfile.TemporaryDirectory() as d:
            out=pathlib.Path(d)
            fixture=ROOT/"fixtures/m7/cooperative_sum.atto"
            sha=hashlib.sha256(fixture.read_bytes()).hexdigest()
            p=subprocess.run([sys.executable,str(COMPILER),str(fixture),
                "--out",str(out),"--source-sha256",sha],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            m=json.loads((out/"manifest.json").read_text())
            raw=(out/"m8-typed-ir.json").read_bytes()
            self.assertEqual(m["source_sha256"],sha)
            self.assertEqual(m["m8_ir_sha256"],hashlib.sha256(raw).hexdigest())
            self.assertEqual(m["m8_ir_schema"],"atto.m8.cooperative-ir.v1")
            self.assertEqual(m["java_sha256"],
                             hashlib.sha256((out/"M7GeneratedActor.java").read_bytes()).hexdigest())
            self.assertFalse(m["canonical_compiler"])
if __name__=="__main__":unittest.main()
