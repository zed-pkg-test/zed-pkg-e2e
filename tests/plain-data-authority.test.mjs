import test from "node:test";
import assert from "node:assert/strict";
import {validateClosure,validateRegistry} from "../src/contract.mjs";
import {loadProfiles} from "../src/load-registry.mjs";

const registry=await loadProfiles();
const base={
 schemaVersion:1,target:"browser",backend:"wasm",entry:"main",
 grants:["clock"],
 nodes:[{id:"main",targets:["browser"],requires:["clock"],imports:[]}]
};

test("ordinary JSON records remain admissible",()=>{
 assert.equal(validateRegistry(registry).ok,true);
 assert.equal(validateClosure(base,registry).ok,true);
});

test("authority-bearing root and node may not inherit custom prototype behavior",()=>{
 const pollutedRoot=Object.assign(Object.create({privileged:true}),base);
 const root=validateClosure(pollutedRoot,registry);
 assert.equal(root.ok,false);
 assert.match(root.errors.join("\n"),/resolved graph must be an object/);

 const pollutedNode=Object.assign(Object.create({shadowGrant:"fs.write"}),base.nodes[0]);
 const node=validateClosure({...base,nodes:[pollutedNode]},registry);
 assert.equal(node.ok,false);
 assert.match(node.errors.join("\n"),/plain data object/);
});

test("registry authority records may not be class instances or prototype-derived objects",()=>{
 const polluted=Object.assign(Object.create({override:"net.server"}),registry[0]);
 const input=registry.slice();
 input[0]=polluted;
 const report=validateRegistry(input);
 assert.equal(report.ok,false);
 assert.match(report.errors.join("\n"),/plain data object/);
});

test("null-prototype objects with explicit own data are acceptable",()=>{
 const root=Object.assign(Object.create(null),base);
 const node=Object.assign(Object.create(null),base.nodes[0]);
 const result=validateClosure({...root,nodes:[node]},registry);
 assert.equal(result.ok,true,result.errors.join("\n"));
});

test("revoked authority-record proxies reject cleanly instead of throwing",()=>{
 const root=Proxy.revocable({...base},{});
 root.revoke();
 assert.doesNotThrow(()=>{
   const result=validateClosure(root.proxy,registry);
   assert.equal(result.ok,false);
 });
 const node=Proxy.revocable({...base.nodes[0]},{});
 node.revoke();
 assert.doesNotThrow(()=>{
   const result=validateClosure({...base,nodes:[node.proxy]},registry);
   assert.equal(result.ok,false);
 });
 const record=Proxy.revocable({...registry[0]},{});
 record.revoke();
 assert.doesNotThrow(()=>{
   const result=validateRegistry([record.proxy,...registry.slice(1)]);
   assert.equal(result.ok,false);
 });
});

test("revoked authority-bearing arrays reject cleanly instead of throwing",()=>{
 const grants=Proxy.revocable(["clock"],{});
 grants.revoke();
 assert.doesNotThrow(()=>{
   const result=validateClosure({...base,grants:grants.proxy},registry);
   assert.equal(result.ok,false);
 });
 const profiles=Proxy.revocable([...registry],{});
 profiles.revoke();
 assert.doesNotThrow(()=>{
   const result=validateRegistry(profiles.proxy);
   assert.equal(result.ok,false);
 });
});

test("transparent and hostile proxy policy records reject before any getter trap",()=>{
 let reads=0;
 const trap={get(target,key,receiver){reads++;return Reflect.get(target,key,receiver)}};
 for(const policy of [
   new Proxy({...base},trap),
   {...base,nodes:[new Proxy({...base.nodes[0]},trap)]},
   {...base,grants:new Proxy(["clock"],trap)},
   {...base,nodes:new Proxy([...base.nodes],trap)}
 ]) {
   assert.equal(validateClosure(policy,registry).ok,false);
 }
 const reg=[...registry];
 reg[0]=new Proxy({...registry[0]},trap);
 assert.equal(validateRegistry(reg).ok,false);
 assert.equal(reads,0,"policy inspection must not invoke a user-defined getter trap");
});

test("revoked nodes array rejects before length access",()=>{
 const nodes=Proxy.revocable([...base.nodes],{});
 nodes.revoke();
 assert.doesNotThrow(()=>{
   assert.equal(validateClosure({...base,nodes:nodes.proxy},registry).ok,false);
 });
});
