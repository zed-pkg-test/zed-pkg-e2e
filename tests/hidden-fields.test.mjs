import test from 'node:test';
import assert from 'node:assert/strict';
import {validateClosure,validateRegistry} from '../src/contract.mjs';
import {loadProfiles} from '../src/load-registry.mjs';

test('non-enumerable fields invalidate JSON-shaped policy records',async()=>{
 const profiles=await loadProfiles();
 const base={schemaVersion:1,target:'ebpf',backend:'llvm',entry:'x',grants:[],nodes:[{id:'x',targets:['ebpf'],requires:[],imports:[]}]};
 for(const target of [structuredClone(base),structuredClone(base).nodes[0]]){
  Object.defineProperty(target,'metadata',{value:'opaque'});
  if(target.nodes)assert.equal(validateClosure(target,profiles).ok,false);
  else assert.equal(validateClosure({...base,nodes:[target]},profiles).ok,false);
 }
 const reg=structuredClone(profiles);
 Object.defineProperty(reg[0],'metadata',{value:'opaque'});
 assert.equal(validateRegistry(reg).ok,false);
 const hiddenRequired=structuredClone(base);
 Object.defineProperty(hiddenRequired,'grants',{value:[],enumerable:false});
 assert.equal(validateClosure(hiddenRequired,profiles).ok,false);
});
