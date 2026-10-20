import test from "node:test";
import assert from "node:assert/strict";
import {validateClosure} from "../src/contract.mjs";
import {loadProfiles} from "../src/load-registry.mjs";

// Issue #21: guard the freestanding *admission* layer. These resolved graph
// cases do not prove the source compiler inferred effects or emitted an ELF.
const profiles=await loadProfiles();
const unit=(id,requires=[],imports=[])=>({id,targets:["embedded"],requires,imports});
const graph=(nodes,grants=[])=>({
  schemaVersion:1,entry:nodes[0].id,target:"embedded",backend:"llvm",grants,nodes
});
const admit=(nodes,grants=[])=>validateClosure(graph(nodes,grants),profiles);

test("freestanding core requires no allocator, libc or OS authority",()=>{
  const result=admit([unit("core::numeric")]);
  assert.equal(result.ok,true,result.errors.join("; "));
  assert.equal(result.visited,1);
});

test("core cannot silently import alloc or stdlib effects",()=>{
  for(const required of ["heap","fs.read","fs.write","clock","threads","interop.native"]){
    const result=admit([
      unit("core::numeric",[],["dependency"]),
      unit("dependency",[required])
    ]);
    assert.equal(result.ok,false,required);
    assert.match(result.errors.join("\n"),/imported requirements|missing build grants/);
  }
});

test("alloc heap admission is explicit and cannot be inherited by core",()=>{
  const units=[unit("alloc::vec",["heap"],["core::numeric"]),unit("core::numeric")];
  assert.match(admit(units).errors.join("\n"),/missing build grants/);
  const admitted=admit(units,["heap"]);
  assert.equal(admitted.ok,true,admitted.errors.join("; "));
  assert.equal(admit([unit("core::numeric")],["heap"]).ok,false,
    "a no-heap core graph must not accept an unused ambient heap grant");
});

test("Wasm, WIT and build-time schemas are not implicit bare-metal privileges",()=>{
  const base=graph([unit("core::numeric")]);
  assert.equal(validateClosure({...base,backend:"wasm"},profiles).ok,false);
  for(const forbidden of ["interop.java","net.client","dynamic.code","gc"]){
    assert.equal(admit([unit("core::numeric",[forbidden])],[forbidden]).ok,false,forbidden);
  }
});
