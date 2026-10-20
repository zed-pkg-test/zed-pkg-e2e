import test from "node:test";
import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import {readFile,mkdtemp,writeFile,symlink,rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {validateRegistry,validateClosure,CAPABILITIES,ENVIRONMENTS,BACKENDS} from "../src/contract.mjs";
import {loadProfiles} from "../src/load-registry.mjs";
const profiles=await loadProfiles();
const good={schemaVersion:1,entry:"app",target:"browser",backend:"wasm",grants:["clock","net.client"],nodes:[{id:"app",targets:["browser","server"],requires:["clock","net.client"],imports:["dep"]},{id:"dep",targets:["browser","server"],requires:["net.client"],imports:[]}]};
const check=data=>validateClosure(data,profiles);
test("versioned target profiles conform to vocabulary",()=>assert.deepEqual(validateRegistry(profiles),{ok:true,errors:[]}));
test("portable graph passes",()=>assert.equal(check(good).ok,true));
test("cycles terminate safely when both directions declare transitive requirements",()=>assert.equal(check({...good,nodes:[good.nodes[0],{...good.nodes[1],requires:good.nodes[0].requires,imports:["app"]}]}).ok,true));
test("import target coverage is checked across all promised targets",()=>{const r=check({...good,nodes:[good.nodes[0],{...good.nodes[1],targets:["browser"]}]});assert.match(r.errors.join("\n"),/imported targets/)});
test("dependency cannot escalate declared effects",()=>{const r=check({...good,nodes:[good.nodes[0],{...good.nodes[1],requires:["net.server"]}]});assert.match(r.errors.join("\n"),/requirements/)});
test("capability budget cannot exceed host profile",()=>{const r=check({...good,grants:["interop.java"]});assert.match(r.errors.join("\n"),/grant unavailable/)});
test("build must grant all transitive effects",()=>{const r=check({...good,grants:["net.client"]});assert.match(r.errors.join("\n"),/missing build grants|resolved graph must be an object/)});
test("unknown imports, target and backend fail closed",()=>{for(const changed of [{...good,target:"anywhere"},{...good,backend:"dex"},{...good,nodes:[{...good.nodes[0],imports:["missing"]},good.nodes[1]]}])assert.equal(check(changed).ok,false)});
test("duplicate/unknown capabilities and unreachable units rejected",()=>{for(const changed of [{...good,grants:["clock","clock"]},{...good,grants:["clock","superuser"]},{...good,nodes:[...good.nodes,{id:"extra",targets:["server"],requires:[],imports:[]}]}])assert.equal(check(changed).ok,false)});
test("CLI examples run",()=>{const cli=new URL("../bin/oreslang-target.mjs",import.meta.url).pathname;const fixture=new URL("../examples/portable-closure.json",import.meta.url).pathname;const p=spawnSync(process.execPath,[cli,"check-closure",fixture,"--target=browser","--backend=wasm","--grants=clock,net.client"],{encoding:"utf8"});assert.equal(p.status,0,p.stderr);assert.match(p.stdout,/2 units/)});

test("graph schema and backend must be explicit",()=>{
 assert.match(check({...good,schemaVersion:2}).errors.join("\n"),/schemaVersion/);
 assert.match(check({...good,backend:undefined}).errors.join("\n"),/backend/);
});
test("deep linear import chains do not overflow the verifier",()=>{
 const n=3000;
 const nodes=Array.from({length:n},(_,i)=>({id:"unit"+i,targets:["browser"],requires:[],imports:i===n-1?[]:["unit"+(i+1)]}));
 const r=check({...good,entry:"unit0",grants:[],nodes});
 assert.equal(r.ok,true,r.errors.join("; "));
 assert.equal(r.visited,n);
});
test("oversized graph and excessive fanout are refused",()=>{
 assert.match(check({...good,nodes:Array.from({length:4097},(_,i)=>({id:"unit"+i,targets:["browser"],requires:[],imports:[]}))}).errors.join("\n"),/4096/);
 assert.match(check({...good,nodes:[{...good.nodes[0],imports:Array.from({length:257},(_,i)=>"x"+i)},good.nodes[1]]}).errors.join("\n"),/excessive/);
});

test("invalid top-level closures fail closed without throwing",()=>{
 for(const invalid of [null,undefined,17,"graph",[],{}]){
  const r=check(invalid);
  assert.equal(r.ok,false);
  assert.ok(r.errors.length>0);
 }
});
test("unknown fields and inherited missing required fields fail closed",()=>{
 assert.match(check({...good,unreviewedEffects:["fs.write"]}).errors.join("\\n"),/unknown resolved graph property/);
 const inherited=Object.create(good);
 assert.match(check(inherited).errors.join("\\n"),/missing resolved graph property|resolved graph must be an object/);
});
test("registry notes must match the JSON schema's string contract",()=>{
 const altered=profiles.map(x=>({...x}));
 altered[0].notes={unsafe:"nonstring"};
 assert.match(validateRegistry(altered).errors.join("\\n"),/notes must be a string/);
});


test("registry profiles require own authorization declarations",()=>{
 const inherited=profiles.map(x=>({...x}));
 inherited[0]=Object.create(profiles[0]);
 const result=validateRegistry(inherited);
 assert.equal(result.ok,false);
 assert.match(result.errors.join("\n"),/missing own registry fields|expected plain data object/);
});
test("graph nodes cannot inherit capabilities or identities",()=>{
 const r=check({...good,nodes:[Object.create(good.nodes[0]),good.nodes[1]]});
 assert.equal(r.ok,false);
 assert.match(r.errors.join("\n"),/missing own node fields|expected plain data object/);
});
test("inherited optional build grants cannot authorize graph",()=>{
 const graph=Object.assign(Object.create({grants:["clock","net.client"]}),{
  schemaVersion:1,nodes:good.nodes,entry:"app",target:"browser",backend:"wasm"
 });
 const denied=check(graph);
 assert.equal(denied.ok,false);
 assert.match(denied.errors.join("\n"),/missing build grants|resolved graph must be an object/);
 assert.equal(check({...graph,grants:["clock","net.client"]}).ok,true);
});
test("sparse lists and inherited array slots fail closed",()=>{
 const hole=Array(1);
 for(const changed of [
  {...good,grants:hole},
  {...good,nodes:[{...good.nodes[0],requires:hole},good.nodes[1]]},
  {...good,nodes:[{...good.nodes[0],imports:hole},good.nodes[1]]},
  {...good,nodes:[{...good.nodes[0],targets:hole},good.nodes[1]]},
 ])assert.equal(check(changed).ok,false);
 const registry=profiles.map(x=>({...x}));
 registry[0].capabilities=hole;
 assert.equal(validateRegistry(registry).ok,false);
 const inheritedIndex=Array(1);
 const proto=Object.create(Array.prototype);
 proto[0]="clock";
 Object.setPrototypeOf(inheritedIndex,proto);
 assert.equal(check({...good,grants:inheritedIndex}).ok,false);
});

test("method-shadowing grants cannot authorize ungranted dependency effects",()=>{
 const grants=["clock"];
 grants.includes=()=>true;
 assert.equal(check({...good,grants}).ok,false);
 const inheritedMethod=["clock"];
 Object.setPrototypeOf(inheritedMethod,Object.assign(Object.create(Array.prototype),{includes:()=>true}));
 assert.equal(check({...good,grants:inheritedMethod}).ok,false);
});
test("shadowed every cannot forge requirements or hide graph nodes",()=>{
 const fakeRequires=["net.client"];
 fakeRequires.every=()=>true;
 const forged={...good,grants:["clock"],nodes:[
  {...good.nodes[0],requires:fakeRequires,imports:[]}
 ]};
 assert.equal(check(forged).ok,false);
 const nodes=[
  {id:"app",targets:["browser"],requires:[],imports:[]},
  {id:"hidden",targets:["server"],requires:["interop.java"],imports:[]}
 ];
 nodes.entries=function*(){yield [0,nodes[0]]};
 assert.equal(check({...good,grants:[],nodes}).ok,false);
});
test("accessor-supplied authority cannot change across validation phases",()=>{
 const grants=["clock"];Object.defineProperty(grants,"0",{get(){return "clock"},enumerable:true,configurable:true});
 assert.equal(check({...good,grants}).ok,false);
 const graph={...good};
 Object.defineProperty(graph,"grants",{enumerable:true,get(){return ["clock","net.client"]}});
 assert.equal(check(graph).ok,false);
 const node={...good.nodes[0]};
 Object.defineProperty(node,"requires",{enumerable:true,get(){return ["clock","net.client"]}});
 assert.equal(check({...good,nodes:[node,good.nodes[1]]}).ok,false);
});


test("accessor-backed grants are never executed or admitted",()=>{
 let called=0;
 const hostile={...good};
 Object.defineProperty(hostile,"grants",{enumerable:true,get(){called++;return ["clock","net.client"]}});
 const result=check(hostile);
 assert.equal(result.ok,false);
 assert.match(result.errors.join("\\n"),/resolved graph must be an object/);
 assert.equal(called,0);
});
test("accessor-backed required graph and node fields fail before evaluation",()=>{
 let called=0;
 const hostile={...good};
 Object.defineProperty(hostile,"nodes",{enumerable:true,get(){called++;return good.nodes}});
 const graph=check(hostile);
 assert.equal(graph.ok,false);
 assert.match(graph.errors.join("\\n"),/resolved graph must be an object/);
 const node={...good.nodes[0]};
 Object.defineProperty(node,"requires",{enumerable:true,get(){called++;return []}});
 const result=check({...good,nodes:[node,good.nodes[1]]});
 assert.equal(result.ok,false);
 assert.match(result.errors.join("\\n"),/expected plain data object/);
 assert.equal(called,0);
});
test("accessor-backed profile capabilities and optional notes cannot execute",()=>{
 let called=0;
 const registry=profiles.map(p=>({...p}));
 Object.defineProperty(registry[0],"capabilities",{enumerable:true,get(){called++;return []}});
 const required=validateRegistry(registry);
 assert.equal(required.ok,false);
 assert.match(required.errors.join("\\n"),/expected plain data object/);
 const registry2=profiles.map(p=>({...p}));
 Object.defineProperty(registry2[0],"notes",{enumerable:true,get(){called++;return "injected"}});
 const optional=validateRegistry(registry2);
 assert.equal(optional.ok,false);
 assert.match(optional.errors.join("\\n"),/expected plain data object/);
 assert.equal(called,0);
});


test("malformed symbol and hostile graph selectors reject without coercion",()=>{
 const neverCoerce={toString(){throw Error("selector coerced")},valueOf(){throw Error("selector coerced")}};
 for(const change of [
  {entry:Symbol("app")},{entry:neverCoerce},
  {target:Symbol("browser")},{target:neverCoerce},
  {backend:Symbol("wasm")},{backend:neverCoerce}
 ]){
  const response=check({...good,...change});
  assert.equal(response.ok,false,JSON.stringify(Object.keys(change)));
  assert.ok(response.errors.length>0);
 }
});


test("NUL in graph entry is rejected independently from missing map identity",()=>{
 const invalid=check({...good,entry:"app"+String.fromCharCode(0)});
 assert.equal(invalid.ok,false);
 assert.match(invalid.errors.join("\\n"),/invalid resolved graph entry/);
});


test("resolved-closure JSON Schema 2020-12 vocabulary is pinned to runtime admission",async()=>{
 const schema=JSON.parse(await readFile(new URL("../schemas/resolved-closure.schema.json",import.meta.url),"utf8"));
 assert.equal(schema.$schema,"https://json-schema.org/draft/2020-12/schema");
 assert.equal(schema.type,"object");
 assert.equal(schema.additionalProperties,false);
 assert.deepEqual(schema.required.sort(),["schemaVersion","nodes","entry","target","backend"].sort());
 assert.deepEqual(schema.$defs.environment.enum,ENVIRONMENTS);
 assert.deepEqual(schema.$defs.backend.enum,BACKENDS);
 assert.deepEqual(schema.$defs.capability.enum,CAPABILITIES);
 assert.equal(schema.properties.nodes.maxItems,4096);
 assert.equal(schema.properties.nodes.minItems,1);
 assert.equal(schema.$defs.node.additionalProperties,false);
 assert.deepEqual(schema.$defs.node.required.sort(),["id","targets","requires","imports"].sort());
 assert.equal(schema.$defs.node.properties.imports.maxItems,256);
 assert.equal(schema.$defs.unitId.maxLength,512);
 assert.equal(new RegExp(schema.$defs.unitId.pattern).test("app"+String.fromCharCode(0)),false);
 assert.equal(new RegExp(schema.$defs.unitId.pattern).test("app"),true);
 assert.equal(schema.properties.grants.uniqueItems,true);
 for(const key of ["targets","requires","imports"])
  assert.equal(schema.$defs.node.properties[key].uniqueItems,true);
 // The schema describes shape; the runtime still checks graph edges,
 // transitive effects and that all declared nodes are reachable.
 assert.equal(validateClosure(good,profiles).ok,true);
});

test("FPGA/ASIC pure import closure admitted to HDL; no OS capability grants",()=>{
 for(const target of ["fpga","asic"]){
  const pure={schemaVersion:1,entry:"adder",target,backend:"hdl",grants:[],nodes:[
    {id:"adder",targets:["fpga","asic"],requires:[],imports:["fixedmath"]},
    {id:"fixedmath",targets:["fpga","asic"],requires:[],imports:[]}
  ]};
  assert.equal(check(pure).ok,true,JSON.stringify(check(pure).errors));
  assert.equal(check({...pure,backend:"llvm"}).ok,false);
  assert.equal(check({...pure,grants:["heap"]}).ok,false);
  assert.equal(check({...pure,nodes:[{...pure.nodes[0],requires:["heap"]},pure.nodes[1]]}).ok,false);
  assert.equal(check({...pure,nodes:[{...pure.nodes[0],imports:["dynamic"]},pure.nodes[1]]}).ok,false);
 }
});

test("LiteGraph profile is Wasm-only and excludes ambient host authority",()=>{
 const p=profiles.find(x=>x.name==="litegraph");
 assert.ok(p,"litegraph profile missing");
 assert.deepEqual(p.backends,["wasm"]);
 assert.ok(p.capabilities.includes("heap"));
 for(const denied of ["gc","fs.read","fs.write","dynamic.code","interop.native","interop.java","threads"])
  assert.equal(p.capabilities.includes(denied),false,denied+" must stay fail-closed");
 const graph={schemaVersion:1,entry:"actor",target:"litegraph",backend:"wasm",
  grants:["clock","heap"],nodes:[{id:"actor",targets:["litegraph"],requires:["clock","heap"],imports:[]}]};
 assert.equal(validateClosure(graph,profiles).ok,true);
 assert.equal(validateClosure({...graph,backend:"llvm"},profiles).ok,false);
 assert.equal(validateClosure({...graph,grants:[]},profiles).ok,false);
 for(const cap of ["gc","fs.read","fs.write","dynamic.code","interop.native","interop.java","threads"]) {
  const bad={...graph,grants:[cap],nodes:[{id:"actor",targets:["litegraph"],requires:[cap],imports:[]}]};
  assert.equal(validateClosure(bad,profiles).ok,false);
 }
});


test("eBPF policy denies ambient OS authority and requires packet-read grant",()=>{
 const bpf=profiles.find(p=>p.name==="ebpf");
 assert.ok(bpf);
 assert.deepEqual(bpf.backends,["llvm"]);
 assert.deepEqual(bpf.capabilities,["bpf.packet.read"]);
 const node={id:"filter",targets:["ebpf"],requires:[],imports:[]};
 const graph={schemaVersion:1,entry:"filter",target:"ebpf",backend:"llvm",grants:[],nodes:[node]};
 assert.equal(check(graph).ok,true);
 for(const denied of ["heap","gc","threads","interop.native","dynamic.code","fs.read","net.server"]){
  assert.equal(check({...graph,grants:[denied],nodes:[{...node,requires:[denied]}]}).ok,false,denied);
 }
 assert.equal(check({...graph,backend:"hdl"}).ok,false);
 assert.equal(check({...graph,nodes:[{...node,requires:["bpf.packet.read"]}]}).ok,false);
 const packet={...graph,grants:["bpf.packet.read"],nodes:[{...node,requires:["bpf.packet.read"]}]};
 assert.equal(check(packet).ok,true,check(packet).errors.join("; "));
 const imported={...packet,nodes:[{...packet.nodes[0],imports:["reader"]},
  {id:"reader",targets:["ebpf"],requires:["bpf.packet.read"],imports:[]}]};
 assert.equal(check(imported).ok,true,check(imported).errors.join("; "));
 assert.equal(check({...imported,nodes:[{...imported.nodes[0],requires:[]},imported.nodes[1]]}).ok,false);
});

test("carrier Linux is not a hardware backend or a kernel-BPF profile",()=>{
 const carrier=profiles.find(p=>p.name==="carrier-linux");
 assert.ok(carrier);
 assert.deepEqual(carrier.backends,["llvm","graal"]);
 for(const forbidden of ["bpf.packet.read","device.gpio","device.camera","interop.java"])
  assert.equal(carrier.capabilities.includes(forbidden),false);
 const node={id:"main",targets:["carrier-linux"],requires:["threads","heap"],imports:[]};
 const graph={schemaVersion:1,entry:"main",target:"carrier-linux",backend:"llvm",grants:["threads","heap"],nodes:[node]};
 assert.equal(check(graph).ok,true,check(graph).errors.join("; "));
 for(const backend of ["hdl","wasm","javascript","dex"])
  assert.equal(check({...graph,backend}).ok,false);
 assert.equal(check({...graph,grants:[]}).ok,false);
 assert.equal(check({...graph,grants:["threads","heap","bpf.packet.read"]}).ok,false);
});

test("compiler-shaped XDP closure requires explicit external grant",()=>{
 const cli=new URL("../bin/oreslang-target.mjs",import.meta.url).pathname;
 const root=new URL("../examples/",import.meta.url);
 const cmd=(file,grants)=>spawnSync(process.execPath,[
   cli,"check-closure",new URL(file,root).pathname,
   "--target=ebpf","--backend=llvm","--grants="+grants
 ],{encoding:"utf8"});
 const constant=cmd("bpf-xdp-constant-closure.json","");
 assert.equal(constant.status,0,constant.stderr);
 const noGrant=cmd("bpf-xdp-packet-closure.json","");
 assert.equal(noGrant.status,1,noGrant.stdout+" "+noGrant.stderr);
 assert.match(noGrant.stderr,/missing build grants/);
 const permitted=cmd("bpf-xdp-packet-closure.json","bpf.packet.read");
 assert.equal(permitted.status,0,permitted.stderr);
 const extra=cmd("bpf-xdp-constant-closure.json","bpf.packet.read");
 assert.equal(extra.status,1,"unused privileges must be rejected");
});

test("CLI refuses duplicated or missing authority selectors",()=>{
 const cli=new URL("../bin/oreslang-target.mjs",import.meta.url).pathname;
 const fixture=new URL("../examples/bpf-xdp-constant-closure.json",import.meta.url).pathname;
 const invoke=(flags)=>spawnSync(process.execPath,[cli,"check-closure",fixture,...flags],
   {encoding:"utf8",timeout:5000});
 const good=invoke(["--target=ebpf","--backend=llvm","--grants="]);
 assert.equal(good.status,0,good.stderr);
 for(const args of [
   ["--target=ebpf","--target=carrier-linux","--backend=llvm"],
   ["--target=ebpf","--backend=llvm","--backend=hdl"],
   ["--target=ebpf","--backend=llvm","--grants=","--grants=bpf.packet.read"],
   ["--backend=llvm"],["--target=ebpf"],["--target=ebpf","--backend=llvm","--g=1"]
 ]){
   const result=invoke(args);
   assert.equal(result.status,2,"unexpected selector admission: "+args+" "+result.stderr);
   assert.equal(result.stdout,"");
 }
 for(const command of [["list","garbage"],["validate-registry","--backend=llvm"]]){
  const result=spawnSync(process.execPath,[cli,...command],
    {encoding:"utf8",timeout:5000});
  assert.equal(result.status,2,result.stdout+" "+result.stderr);
 }
});

test("CLI safely rejects FIFO, symlink, directory and oversized or malformed graph",{
 skip:process.platform!=="linux"
},async()=>{
 const cli=new URL("../bin/oreslang-target.mjs",import.meta.url).pathname;
 const fixture=await readFile(new URL("../examples/bpf-xdp-constant-closure.json",import.meta.url));
 const dir=await mkdtemp(join(tmpdir(),"ores-target-admission-"));
 const invoke=(p)=>spawnSync(process.execPath,[
   cli,"check-closure",p,"--target=ebpf","--backend=llvm","--grants="
 ],{encoding:"utf8",timeout:5000});
 try {
  const good=join(dir,"valid.json");await writeFile(good,fixture);
  assert.equal(invoke(good).status,0);
  const linked=join(dir,"linked.json");await symlink(good,linked);
  assert.equal(invoke(linked).status,2,"final-component symlink must be denied");
  assert.equal(invoke(dir).status,2,"directory must be denied");
  const fifo=join(dir,"named-pipe.json");
  const mk=spawnSync("mkfifo",[fifo],{encoding:"utf8",timeout:5000});
  assert.equal(mk.status,0,mk.stderr);
  const result=invoke(fifo);
  assert.equal(result.status,2,"FIFO admission must fail without blocking: "+result.stderr);
  assert.notEqual(result.error?.code,"ETIMEDOUT","FIFO open must never hang");
  const big=join(dir,"too-large.json");
  await writeFile(big,Buffer.alloc(16*1024*1024+1,32));
  const oversized=invoke(big);
  assert.equal(oversized.status,2,oversized.stderr);
  assert.match(oversized.stderr,/exceeds 16 MiB/);
  const invalid=join(dir,"invalid-utf8.json");
  await writeFile(invalid,Buffer.from([0xc0,0xaf]));
  assert.equal(invoke(invalid).status,2,"overlong UTF-8 must fail");
 }finally{await rm(dir,{recursive:true,force:true})}
});

test("compiler target identity and ungranted eBPF closure cannot be silently rewritten",async()=>{
 const cli=new URL("../bin/oreslang-target.mjs",import.meta.url).pathname;
 const fixture=JSON.parse(await readFile(
   new URL("../examples/bpf-xdp-packet-closure.json",import.meta.url),"utf8"));
 const dir=await mkdtemp(join(tmpdir(),"ores-bpf-identity-"));
 const graphPath=join(dir,"graph.json");
 const run=(grants="bpf.packet.read",target="ebpf",backend="llvm")=>
   spawnSync(process.execPath,[cli,"check-closure",graphPath,
     "--target="+target,"--backend="+backend,"--grants="+grants],
     {encoding:"utf8",timeout:5000});
 try{
  await writeFile(graphPath,JSON.stringify(fixture));
  const valid=run();
  assert.equal(valid.status,0,valid.stderr);
  const bad=[
   {...fixture,target:"carrier-linux"},
   {...fixture,backend:"graal"},
   {...fixture,grants:["bpf.packet.read"]},
   {...fixture,grants:null},
   {...fixture,grants:undefined}
  ];
  for(const graph of bad){
   await writeFile(graphPath,JSON.stringify(graph));
   const result=run();
   assert.equal(result.status,2,
    "forged or self-granted compiler closure accepted: "+JSON.stringify(graph));
   assert.equal(result.stdout,"");
  }
  await writeFile(graphPath,JSON.stringify(fixture));
  for(const [target,backend] of [["carrier-linux","llvm"],["ebpf","graal"]]){
   const out=run("bpf.packet.read",target,backend);
   assert.equal(out.status,2,
     "CLI silently rewrote compiler target/backend: "+out.stderr);
   assert.match(out.stderr,/disagrees with build selector/);
  }
 } finally {await rm(dir,{recursive:true,force:true})}
});

test("both v1 JSON schemas pin the exact runtime profile vocabulary",async()=>{
 const profile=JSON.parse(await readFile(new URL("../schemas/profile.schema.json",import.meta.url),"utf8"));
 const closure=JSON.parse(await readFile(new URL("../schemas/resolved-closure.schema.json",import.meta.url),"utf8"));
 assert.deepEqual(profile.properties.name.enum,ENVIRONMENTS,
  "profile.schema environment enum must match runtime");
 assert.deepEqual(profile.properties.backends.items.enum,BACKENDS,
  "profile.schema backend enum must match runtime");
 assert.deepEqual(profile.properties.capabilities.items.enum,CAPABILITIES,
  "profile.schema capability enum must match runtime");
 assert.deepEqual(closure.$defs.environment.enum,ENVIRONMENTS,
  "resolved-closure environment enum must match runtime");
 assert.deepEqual(closure.$defs.backend.enum,BACKENDS,
  "resolved-closure backend enum must match runtime");
 assert.deepEqual(closure.$defs.capability.enum,CAPABILITIES,
  "resolved-closure capability enum must match runtime");
 for(const p of profiles){
  assert.ok(profile.properties.name.enum.includes(p.name),p.name);
  assert.ok(p.backends.every(x=>profile.properties.backends.items.enum.includes(x)),p.name+" backend");
  assert.ok(p.capabilities.every(x=>profile.properties.capabilities.items.enum.includes(x)),p.name+" capability");
 }
});
