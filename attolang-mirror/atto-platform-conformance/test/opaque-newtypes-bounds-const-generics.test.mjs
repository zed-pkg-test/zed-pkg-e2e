// Host-only schema/fixture admission checks. These do not compile or execute AttoLang.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
const root = new URL('../',import.meta.url);
const registry=JSON.parse(readFileSync(new URL('../contracts/opaque-newtypes-bounds-const-generics-v1.json',import.meta.url),'utf8'));
test('newtype+const-generic fixture registry is explicit and non-authorizing',()=>{
  assert.equal(registry.schema,'atto.opaque-newtypes-bounds-const-generics.v1');
  assert.equal(registry.status,'TARGET_NOT_EXECUTED');
  assert.ok(Object.values(registry.backend_status).every(x=>x==='NOT_VERIFIED'));
  assert.equal(new Set(registry.cases.map(x=>x.id)).size,registry.cases.length);
  assert.ok(registry.cases.length>=12);
  for(const c of registry.cases){
    assert.equal(c.verification,'NOT_EXECUTED',c.id);
    assert.ok(c.path.startsWith('fixtures/opaque-newtypes-bounds-const-generics-v1/'),c.id);
    assert.ok(existsSync(new URL('../'+c.path,import.meta.url)),c.path);
    const src=readFileSync(new URL('../'+c.path,import.meta.url),'utf8');
    assert.ok(src.includes('fnc main() -> i64'),c.path);
    assert.ok(!src.includes('TODO')&&!src.includes('...'),c.path);
    if(c.kind==='PASS'){
      assert.equal(c.expected_exit,42,c.id);
      assert.equal(c.expected_error,undefined,c.id);
    } else {
      assert.equal(c.kind,'REJECT',c.id);
      assert.match(c.expected_error,/^E_(NEWTYPE|RANGE|CONST)_/,c.id);
      assert.equal(c.expected_exit,undefined,c.id);
    }
  }
});
test('negative cases cover distinct failure phases',()=>{
  const errors=new Set(registry.cases.filter(c=>c.kind==='REJECT').map(c=>c.expected_error));
  for(const diagnostic of ['E_NEWTYPE_MISMATCH','E_NEWTYPE_CONSTRUCTION','E_RANGE_INVALID','E_RANGE_UNPROVEN','E_CONST_SHAPE_MISMATCH','E_CONST_GENERIC_ARG','E_CONST_OVERFLOW'])
    assert.ok(errors.has(diagnostic),diagnostic);
});

test('executed private nominal Result receipt cannot promote general backend status',()=>{
  assert.equal(registry.status,'TARGET_NOT_EXECUTED');
  assert.equal(registry.cases.length,12);
  assert.ok(registry.cases.every(c=>c.verification==='NOT_EXECUTED'));
  assert.ok(Object.values(registry.backend_status).every(v=>v==='NOT_VERIFIED'));
  const executed=registry.verified_subprofiles.find(
    p=>p.id==='rust-browser-nominal-result-graph-return-match-v0');
  assert.ok(executed,'typed nominal Result subprofile must be individually accounted for');
  assert.equal(executed.status,'FOCUSED_EXECUTED');
  assert.equal(executed.source_head,'f55af346231eb2bcfb766aa18d2f8f59cbfcb664');
  assert.equal(executed.executed.total_rust_tests_passed,101);
  assert.equal(executed.executed.native_wasm32_browser_compiler_byte_identical_guest_passed,6);
  assert.equal(executed.executed.native_wasm32_matching_negative_diagnostic_no_artifact_passed,8);
  assert.equal(executed.source_files_exact_sha_verified+
               executed.conformance_files_exact_sha_verified,98);
  assert.equal(executed.combined_v1_status,'TARGET_NOT_EXECUTED');
  assert.equal(executed.backend_parity,'NOT_VERIFIED');
  assert.ok(executed.excluded.some(s=>s.includes('RangeError lacks owned runtime')));
  assert.ok(executed.excluded.some(s=>s.includes('not general production AST')));
});


test('production nominal AST and checked ? receipt is limited to the Rust/browser subset',()=>{
  const receipt=registry.verified_subprofiles.find(
    p=>p.id==='rust-browser-production-ast-result-checked-propagation-v0');
  assert.ok(receipt,'new production AST and ?-guest receipt must be explicit');
  assert.equal(receipt.status,'FOCUSED_EXECUTED');
  assert.equal(receipt.source_head,'e8fe817541c9282a1b9c7077df8ea3ce5c647b51');
  assert.equal(receipt.executed.total_rust_tests_passed,112);
  assert.equal(receipt.executed.production_ast_generic_result_integration_passed,8);
  assert.equal(receipt.executed.typed_result_propagation_integration_passed,10);
  assert.equal(receipt.executed.native_wasm32_browser_compiler_byte_identical_guests_passed,9);
  assert.equal(receipt.executed.native_wasm32_matching_negative_diagnostic_no_artifact_passed,11);
  assert.equal(receipt.source_files_exact_sha_verified+receipt.conformance_files_exact_sha_verified,99);
  assert.equal(receipt.combined_v1_status,'TARGET_NOT_EXECUTED');
  assert.equal(receipt.backend_parity,'NOT_VERIFIED');
  assert.ok(receipt.excluded.some(x=>x.includes('no owned runtime RangeError')));
  assert.ok(receipt.excluded.some(x=>x.includes('cannot yet generate general guest')));
  assert.ok(receipt.excluded.some(x=>x.includes('no independent Java')));
  assert.equal(registry.status,'TARGET_NOT_EXECUTED');
  assert.ok(registry.cases.every(x=>x.verification==='NOT_EXECUTED'));
  assert.ok(Object.values(registry.backend_status).every(x=>x==='NOT_VERIFIED'));
});
