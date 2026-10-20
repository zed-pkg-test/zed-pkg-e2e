import test from "node:test";
import assert from "node:assert/strict";
import {validateCoreWasmArtifact,CORE_WASM_ABI} from "../src/artifact.mjs";
import {loadProfiles} from "../src/load-registry.mjs";
const profiles=await loadProfiles();
const x="a".repeat(64);
const good={schemaVersion:1,artifactType:CORE_WASM_ABI,target:"wasm32",
 environment:"browser",backend:"wasm",entry:"main",wasmSha256:x,wasmBytes:157,
 requiredCapabilities:[],imports:[],memory:{maximumPages:2},
 producer:{compilerSha256:x,sourceSha256:x,llvmIrSha256:x}};
const check=p=>validateCoreWasmArtifact(p,profiles);
test("canonical pinned pure Wasm guest artifact accepted",()=>assert.deepEqual(check(good),{ok:true,errors:[]}));
test("wasm guest profiles are explicit",()=>{
 for(const environment of ["browser","edge-js","edge-wasi"])assert.equal(check({...good,environment}).ok,true);
 for(const environment of ["server","ios","android","embedded","bad"])assert.equal(check({...good,environment}).ok,false);
});
test("unknown fields, digests, entry, memory bounds and shape rejected",()=>{
 for(const changed of [{...good,secret:true},{...good,wasmSha256:"b"},{...good,entry:"../../x"},
  {...good,memory:{maximumPages:257}},{...good,memory:{maximumPages:2,extra:1}},
  {...good,producer:{...good.producer,compilerSha256:"0"}},
  {...good,wasmBytes:0},{...good,schemaVersion:2},{...good,backend:"llvm"}])
   assert.equal(check(changed).ok,false,JSON.stringify(changed));
});
test("zero-import ABI forbids host calls and capability escalation",()=>{
 assert.equal(check({...good,imports:["ores_host.clock_ms"]}).ok,false);
 assert.equal(check({...good,requiredCapabilities:["clock"]}).ok,false);
 assert.equal(check({...good,requiredCapabilities:["fs.read"]}).ok,false);
});

test("artifact authority rejects inherited fields, accessors, proxies and hidden metadata",()=>{
 let accesses=0;
 const getter={...good};Object.defineProperty(getter,"entry",{enumerable:true,get(){accesses++;throw Error("getter");}});
 const hidden={...good};Object.defineProperty(hidden,"hidden",{value:true});
 const symbol={...good,[Symbol("extra")]:true};
 const malformed=[Object.create(good),getter,hidden,symbol,new Proxy(good,{}),Proxy.revocable(good,{} )];
 const revoked=Proxy.revocable(good,{});revoked.revoke();malformed.push(revoked.proxy);
 for(const x of malformed)assert.equal(check(x).ok,false);
 for(const key of ["memory","producer"])
  assert.equal(check({...good,[key]:Object.create(good[key])}).ok,false);
 const caps=[];Object.defineProperty(caps,"extra",{get(){accesses++;throw Error("getter");}});
 assert.equal(check({...good,requiredCapabilities:caps}).ok,false);
 assert.equal(accesses,0);
});
test("null-prototype artifact records remain valid own data",()=>{
 const value=Object.assign(Object.create(null),good);
 value.memory=Object.assign(Object.create(null),good.memory);
 value.producer=Object.assign(Object.create(null),good.producer);
 assert.equal(check(value).ok,true);
});
