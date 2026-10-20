import {open,readdir} from "node:fs/promises";
import {constants} from "node:fs";
import {TextDecoder} from "node:util";
import {rejectDuplicateJsonKeys} from "./strict-json-keys.mjs";

const directory=new URL("../profiles/",import.meta.url);
const MAX_PROFILE_BYTES=64*1024;

// The registry is authority-bearing policy: reject symlinks, FIFOs, oversized
// or ambiguous UTF-8/JSON before a profile's last-wins keys can affect grants.
export async function readProfileFile(path){
 if(!Number.isInteger(constants.O_NONBLOCK) ||
    !Number.isInteger(constants.O_NOFOLLOW))
  throw Error("secure registry file admission is unsupported on this host");
 const fd=await open(path,constants.O_RDONLY|constants.O_NONBLOCK|
   (constants.O_CLOEXEC||0)|constants.O_NOFOLLOW);
 try{
  const stat=await fd.stat();
  if(!stat.isFile())throw Error("registry profile must be a regular file");
  if(stat.size>MAX_PROFILE_BYTES)throw Error("registry profile exceeds 64 KiB");
  const buffer=Buffer.allocUnsafe(MAX_PROFILE_BYTES+1);
  let total=0;
  for(;;){
   const {bytesRead}=await fd.read(buffer,total,MAX_PROFILE_BYTES+1-total,null);
   if(bytesRead===0)break;
   total+=bytesRead;
   if(total>MAX_PROFILE_BYTES)throw Error("registry profile exceeds 64 KiB");
  }
  const text=new TextDecoder("utf-8",{fatal:true,ignoreBOM:true})
    .decode(buffer.subarray(0,total));
  rejectDuplicateJsonKeys(text);
  return JSON.parse(text);
 }finally{await fd.close()}
}
export async function loadProfiles(){
 const names=(await readdir(directory)).filter(name=>name.endsWith(".json")).sort();
 return Promise.all(names.map(name=>readProfileFile(new URL(name,directory))));
}
