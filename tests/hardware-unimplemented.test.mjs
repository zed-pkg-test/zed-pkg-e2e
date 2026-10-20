import test from 'node:test';
import assert from 'node:assert/strict';
import {validateClosure,validateRegistry} from '../src/contract.mjs';
import {loadProfiles} from '../src/load-registry.mjs';
const registry=await loadProfiles();
test('current registry remains valid',()=>assert.equal(validateRegistry(registry).ok,true));
for(const target of ['fpga','asic']){
 test(target+' rejects generic LLVM CPU backend',()=>{
  const graph={schemaVersion:1,target,backend:'llvm',entry:'add8',grants:[],
   nodes:[{id:'add8',targets:[target],requires:[],imports:[]}]};
  const r=validateClosure(graph,registry);
  assert.equal(r.ok,false,'generic LLVM closure must not silently admit '+target);
  assert.match(r.errors.join('\n'),/backend llvm is not declared/i);
 });
}
