import test from "node:test";
import assert from "node:assert/strict";
import {validateClosure} from "../src/contract.mjs";
import {loadProfiles} from "../src/load-registry.mjs";
const profiles=await loadProfiles();
const graph={
 schemaVersion:1,target:"browser",backend:"wasm",entry:"main",
 grants:["clock"],
 nodes:[{id:"main",targets:["browser"],requires:["clock"],imports:[]}]
};
const check=input=>validateClosure(input,profiles);
test("resolved closure rejects unrecognized top-level fields",()=>{
 const result=check({...graph,unverifiedPermissions:["fs.write"]});
 assert.equal(result.ok,false);
 assert.match(result.errors.join("\n"),/unknown resolved graph property/);
});
test("resolved closure does not throw on invalid root shapes",()=>{
 for(const input of [null,undefined,[],42,"no graph"]){
  const result=check(input);
  assert.equal(result.ok,false);
  assert.match(result.errors.join("\n"),/resolved graph must be an object/);
 }
});
test("a closed graph cannot request authority unused by any linked unit",()=>{
 const result=check({...graph,grants:["clock","net.client"]});
 assert.equal(result.ok,false);
 assert.match(result.errors.join("\n"),/unused build grants: net.client/);
});
test("all explicitly declared and consumed build grants remain valid",()=>{
 assert.equal(check(graph).ok,true);
});
