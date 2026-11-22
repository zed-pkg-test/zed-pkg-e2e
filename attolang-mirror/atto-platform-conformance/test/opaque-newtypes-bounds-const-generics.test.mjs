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
