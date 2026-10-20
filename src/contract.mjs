import {types as nodeTypes} from "node:util";

export const CAPABILITIES = Object.freeze(["clock","random","net.client","net.server","fs.read","fs.write","threads","interop.java","interop.native","device.camera","device.gpio","dynamic.code","heap","gc","bpf.packet.read"]);
export const ENVIRONMENTS = Object.freeze(["server","desktop","browser","edge-js","edge-wasi","ios","android","embedded","fpga","asic","litegraph","ebpf","carrier-linux"]);
export const BACKENDS = Object.freeze(["llvm","graal","wasm","javascript","dex","c","bytecode","hdl"]);
// Authority-bearing lists must be dense, own-indexed, unique JSON-like arrays.
// Array#every skips holes, and inherited numeric entries can otherwise mask
// absent capabilities/targets, making a malformed build graph appear valid.
// JSON-like authority arrays must have ONLY own, non-accessor numeric slots.
// In particular, allow no shadowed includes/every/entries, custom prototypes
// or sparse slots: all can subvert policy checks despite a correct length.
const ownDenseArray=(a,max=4096)=>{
  try{
    if(nodeTypes.isProxy(a)||!Array.isArray(a)||Object.getPrototypeOf(a)!==Array.prototype||a.length>max)return false;
    if(Reflect.ownKeys(a).length!==a.length+1)return false;
    for(let i=0;i<a.length;i++){
      const slot=Object.getOwnPropertyDescriptor(a,String(i));
      if(!slot||!Object.hasOwn(slot,'value'))return false;
    }
    return true;
  }catch{
    // A revoked or hostile Proxy cannot become an authority-bearing array.
    return false;
  }
};
const dataOnly=o=>{
  // A graph authority record is JSON-like data, never a class instance,
  // object with a custom prototype or an object with accessor properties.
  // Custom prototypes can introduce inherited policy behavior *after* this
  // validator returns, even when its immediate own fields looked valid.
  try {
    if(nodeTypes.isProxy(o))return false;
    const prototype=Object.getPrototypeOf(o);
    if(prototype!==Object.prototype&&prototype!==null)return false;
    for(const key of Reflect.ownKeys(o)){
      if(typeof key!=="string")return false;
      const descriptor=Object.getOwnPropertyDescriptor(o,key);
      if(!descriptor||!Object.hasOwn(descriptor,"value")||descriptor.enumerable!==true)return false;
    }
    return true;
  }catch{
    // E.g. a revoked Proxy: fail closed without throwing out of admission.
    return false;
  }
};
const unique = a => {
  if(!ownDenseArray(a))return false;
  const seen=new Set();
  for(let i=0;i<a.length;i++){
    const x=a[i];
    if(typeof x!=='string'||x.length===0||seen.has(x))return false;
    seen.add(x);
  }
  return true;
};
// Never dispatch policy checks through caller-controlled array methods.
const subset=(a,b)=>{
  const allowed=new Set();
  for(let i=0;i<b.length;i++)allowed.add(b[i]);
  for(let i=0;i<a.length;i++)if(!allowed.has(a[i]))return false;
  return true;
};

export function validateRegistry(profiles) {
  const errors=[],seen=new Set();
  if (!ownDenseArray(profiles)) return {ok:false,errors:["registry must be a dense own-indexed array"]};
  for (const [i,p] of profiles.entries()) {
    const at="profiles["+i+"]";
    if (!p || typeof p!=="object" || !dataOnly(p)) {errors.push(at+": expected plain data object");continue}
    const missing=["schemaVersion","name","backends","capabilities"].filter(k=>!Object.hasOwn(p,k));
    if(missing.length){errors.push(at+": missing own registry fields "+missing.join(","));continue}
    if (p.schemaVersion!==1 || !ENVIRONMENTS.includes(p.name)) errors.push(at+": unknown schema or target");
    if (seen.has(p.name)) errors.push(at+": duplicate environment "+p.name);
    seen.add(p.name);
    if (!unique(p.backends)||p.backends.length===0||!subset(p.backends,BACKENDS)) errors.push(at+": invalid backends");
    if (!unique(p.capabilities)||!subset(p.capabilities,CAPABILITIES)) errors.push(at+": invalid capabilities");
    // Optional notes are never taken from a prototype getter.
    const notes=Object.getOwnPropertyDescriptor(p,"notes");
    if(notes&&!Object.hasOwn(notes,"value")) errors.push(at+": notes must be an own data property");
    else if(notes&&notes.value!==undefined&&typeof notes.value!=="string")
      errors.push(at+": notes must be a string");
    for (const k of Object.keys(p)) if (!["schemaVersion","name","backends","capabilities","notes"].includes(k)) errors.push(at+": unknown property "+k);
  }
  for (const name of ENVIRONMENTS) if (!seen.has(name)) errors.push("missing profile "+name);
  return {ok:errors.length===0,errors};
}
export function validateClosure(input,profiles) {
  const errors=[];
  if(!input||typeof input!=="object"||!dataOnly(input))
    return {ok:false,errors:["resolved graph must be an object"]};
  const keys=["schemaVersion","nodes","entry","target","backend","grants"];
  for(const field of Object.keys(input))
    if(!keys.includes(field))errors.push("unknown resolved graph property "+field);
  for(const field of ["schemaVersion","nodes","entry","target","backend"])
    if(!Object.hasOwn(input,field))errors.push("missing resolved graph property "+field);
  // Invalid/missing required data cannot be read from inherited getters.
  if(errors.length)return {ok:false,errors};
  const {schemaVersion,nodes,entry,target,backend}=input;
  // Reject non-string graph selectors before they enter diagnostics,
  // string concatenation or map lookups. Symbol-valued selectors previously
  // threw TypeError instead of returning a fail-closed validation result.
  if(typeof entry!=="string"||entry.length===0||entry.length>512||
     entry.includes("\u0000"))errors.push("invalid resolved graph entry");
  if(typeof target!=="string")errors.push("invalid resolved graph target");
  if(typeof backend!=="string")errors.push("missing or unknown backend");
  if(errors.length)return {ok:false,errors};
  // Optional authority must never be inherited through a prototype chain.
  const grants=Object.hasOwn(input,"grants")?input.grants:[];
  if(schemaVersion!==1)errors.push("unsupported or missing resolved-graph schemaVersion");
  if(typeof backend!=="string"||!BACKENDS.includes(backend))errors.push("missing or unknown backend");
  const registry=validateRegistry(profiles);
  if (!registry.ok) return registry;
  const profile=profiles.find(x=>x.name===target);
  if (!profile) errors.push("unknown requested target: "+target);
  if (backend!==undefined && (!BACKENDS.includes(backend)||!profile?.backends.includes(backend))) errors.push("backend "+backend+" is not declared for "+target);
  if (!unique(grants)||!subset(grants,CAPABILITIES)) errors.push("invalid or duplicate grants");
  if (profile && unique(grants) && !subset(grants,profile.capabilities)) errors.push("capability grant unavailable in "+target);
  // Check for proxies before accessing array.length: revoked proxies
  // can throw even from seemingly innocent shape/size probes.
  if(nodeTypes.isProxy(nodes)||!Array.isArray(nodes))
    return {ok:false,errors:[...errors,"nodes must be a dense own-indexed array"]};
  if(nodes.length===0)return {ok:false,errors:[...errors,"nodes must be a nonempty array"]};
  if(nodes.length>4096)return {ok:false,errors:[...errors,"resolved graph exceeds 4096 units"]};
  if(!ownDenseArray(nodes))
    return {ok:false,errors:[...errors,"nodes must be a dense own-indexed array"]};
  const ids=new Map();
  for(const [i,node] of nodes.entries()){
    const at="nodes["+i+"]";
    if(!node||typeof node!=="object"||!dataOnly(node)){errors.push(at+": expected plain data object");continue}
    const missing=["id","targets","requires","imports"].filter(k=>!Object.hasOwn(node,k));
    if(missing.length){errors.push(at+": missing own node fields "+missing.join(","));continue}
    if(typeof node.id!=="string"||node.id.length===0||node.id.length>512||node.id.includes("\u0000")){errors.push(at+": invalid id");continue}
    if(ids.has(node.id))errors.push(at+": duplicate id "+node.id);
    ids.set(node.id,node);
    if(!unique(node.targets)||node.targets.length===0||!subset(node.targets,ENVIRONMENTS))errors.push(node.id+": invalid targets");
    if(!unique(node.requires)||!subset(node.requires,CAPABILITIES))errors.push(node.id+": invalid requires");
    if(!unique(node.imports)||node.imports.length>256||node.imports.some(x=>x.length>512||x.includes("\u0000")))errors.push(node.id+": invalid or excessive imports");
    for(const field of Object.keys(node))if(!["id","targets","requires","imports"].includes(field))errors.push(node.id+": unknown property "+field);
  }
  if(!ids.has(entry))errors.push("missing entry "+entry);
  if(errors.length) return {ok:false,errors};
  // Iterative graph traversal avoids adversarial recursion depth and ensures
  // cycles terminate. Check the promised target set of *every* import edge,
  // not just compatibility with today's selected compilation target.
  const visited=new Set();
  const pending=[entry];
  let edges=0;
  while(pending.length){
    const id=pending.pop();
    if(visited.has(id))continue;
    visited.add(id);
    const a=ids.get(id);
    if(!a.targets.includes(target))errors.push(id+": target "+target+" not admitted");
    if(!subset(a.requires,grants))errors.push(id+": missing build grants "+a.requires.filter(x=>!grants.includes(x)).join(","));
    if(profile&&!subset(a.requires,profile.capabilities))errors.push(id+": target "+target+" lacks declared capabilities");
    for(const dep of a.imports){
      edges++;
      if(edges>65536)return {ok:false,errors:[...errors,"resolved graph exceeds 65536 import edges"]};
      if(!ids.has(dep)){errors.push(id+": unresolved import "+dep);continue}
      const b=ids.get(dep);
      if(!subset(a.targets,b.targets))errors.push(id+" -> "+dep+": imported targets do not cover importer targets");
      if(!subset(b.requires,a.requires))errors.push(id+" -> "+dep+": imported requirements are not declared by importer");
      if(!visited.has(dep))pending.push(dep);
    }
  }
  if(visited.size!==ids.size) errors.push("graph contains "+(ids.size-visited.size)+" unreachable units; supply exact linked closure");
  // A closed graph may only receive authority declared by a linked unit.
  const used=new Set();
  for(const unit of ids.values())
    for(let i=0;i<unit.requires.length;i++)used.add(unit.requires[i]);
  const unused=[];
  for(let i=0;i<grants.length;i++)
    if(!used.has(grants[i]))unused.push(grants[i]);
  if(unused.length)errors.push("unused build grants: "+unused.join(","));
  return {ok:errors.length===0,errors,visited:visited.size};
}
