#!/usr/bin/env node
import {open} from "node:fs/promises";
import {constants} from "node:fs";
import {TextDecoder} from "node:util";
import {validateRegistry,validateClosure} from "../src/contract.mjs";
import {loadProfiles} from "../src/load-registry.mjs";
import {rejectDuplicateJsonKeys} from "../src/strict-json-keys.mjs";

const MAX_GRAPH_BYTES=16*1024*1024;
// Reject special files *before* reading, without blocking on FIFOs. Keep the
// cumulative bound even when an attacker grows a regular file after fstat.
// Final-component symlinks cannot select a different authority-bearing graph.
async function readGraph(path) {
 // Silent fallback to 0 would turn a non-blocking, no-symlink security
 // boundary into a potentially blocking open on unsupported platforms.
 // Reject the host instead of weakening the file-origin policy.
 if(!Number.isInteger(constants.O_NONBLOCK) ||
    !Number.isInteger(constants.O_NOFOLLOW))
   throw Error("secure graph file admission is unsupported on this host");
 const flags=constants.O_RDONLY|constants.O_NONBLOCK|
   (constants.O_CLOEXEC||0)|constants.O_NOFOLLOW;
 const fd=await open(path,flags);
 try {
  const stat=await fd.stat();
  if(!stat.isFile())throw Error("resolved graph must be a regular file");
  if(stat.size>MAX_GRAPH_BYTES)throw Error("resolved graph exceeds 16 MiB");
  const parts=[];
  const buffer=Buffer.allocUnsafe(64*1024);
  let total=0;
  for(;;){
   const length=Math.min(buffer.length,MAX_GRAPH_BYTES+1-total);
   const {bytesRead}=await fd.read(buffer,0,length,null);
   if(bytesRead===0)break;
   total+=bytesRead;
   if(total>MAX_GRAPH_BYTES)throw Error("resolved graph exceeds 16 MiB");
   parts.push(Buffer.from(buffer.subarray(0,bytesRead)));
  }
  const decoded=new TextDecoder("utf-8",{fatal:true,ignoreBOM:true}).decode(Buffer.concat(parts,total));
  rejectDuplicateJsonKeys(decoded);
  return JSON.parse(decoded);
 }finally{await fd.close()}
}
function parseSelectors(options){
 const args=Object.create(null);
 for(const arg of options){
  const match=/^--(target|backend|grants)=(.*)$/.exec(arg);
  if(!match)throw Error("unexpected option "+arg);
  if(Object.hasOwn(args,match[1]))throw Error("duplicate option --"+match[1]);
  args[match[1]]=match[2];
 }
 if(!args.target||!args.backend)throw Error("check-closure requires --target and --backend");
 return args;
}
const [command,file,...options]=process.argv.slice(2);
try{
 const profiles=await loadProfiles();
 let result;
 if(command==="list"||command==="validate-registry"){
  if(file!==undefined||options.length!==0)throw Error(command+" accepts no positional/flag arguments");
  if(command==="list"){
   for(const p of profiles)console.log(p.name+"\t"+p.backends.join(","));
   process.exit(0);
  }
  result=validateRegistry(profiles);
 }else if(command==="check-closure"){
  if(!file||file.startsWith("--"))throw Error("expected graph JSON path");
  const args=parseSelectors(options);
  const graph=await readGraph(file);
  // Command-line build policy is authoritative; a compiler-declared target
  // or backend must agree instead of being silently overwritten.
  for(const field of ["target","backend"]){
   if(graph!==null && typeof graph==="object" &&
      Object.hasOwn(graph,field) && graph[field]!==args[field])
    throw Error("resolved graph "+field+" disagrees with build selector");
  }
  if(args.target==="ebpf"){
   // The narrow LLVM XDP compiler issues an *ungranted* effect declaration.
   // Never reinterpret a caller-supplied graph with embedded privileges as
   // an authentic compiler-produced packet policy closure.
   if(graph===null || typeof graph!=="object" ||
      graph.target!=="ebpf" || graph.backend!=="llvm" ||
      !Array.isArray(graph.grants) || graph.grants.length!==0)
    throw Error("eBPF compiler closure must declare ebpf/llvm with no grants");
  }
  result=validateClosure({...graph,target:args.target,backend:args.backend,
    grants:args.grants?args.grants.split(","):[]},profiles);
 }else throw Error("usage: list | validate-registry | check-closure <graph.json> --target=browser --backend=wasm --grants=clock,net.client");
 if(!result.ok){console.error(result.errors.join("\n"));process.exitCode=1}
 else console.log("OK"+(result.visited?" ("+result.visited+" units checked)":""));
}catch(e){console.error(e.message);process.exitCode=2}
