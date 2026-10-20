import test from "node:test";
import assert from "node:assert/strict";
import {mkdtemp,writeFile,symlink,rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {spawnSync} from "node:child_process";
import {readProfileFile,loadProfiles} from "../src/load-registry.mjs";
import {validateRegistry} from "../src/contract.mjs";

test("canonical checked-in profiles still admit exactly one validated registry",async()=>{
 const profiles=await loadProfiles();
 const verdict=validateRegistry(profiles);
 assert.equal(verdict.ok,true,verdict.errors.join("; "));
 assert.equal(profiles.length,13);
});

test("authority-bearing profile JSON rejects duplicates, BOM and invalid UTF-8",async()=>{
 const dir=await mkdtemp(join(tmpdir(),"ores-registry-json-"));
 const path=join(dir,"policy.json");
 try{
  const valid='{"schemaVersion":1,"name":"ebpf","backends":["llvm"],"capabilities":[]}';
  await writeFile(path,valid);
  assert.deepEqual((await readProfileFile(path)).capabilities,[]);
  for(const [source,reason] of [
   ['{"schemaVersion":1,"name":"ebpf","name":"server","backends":["llvm"],"capabilities":[]}',/duplicate object key/],
   ['{"name":"ebpf","na\\u006de":"server"}',/duplicate object key/],
   ['{"name":"ebpf","capabilities":["clock"],"capabilities":["heap"]}',/duplicate object key/],
   [String.fromCharCode(0xfeff)+valid,/JSON BOM/],
  ]){
   await writeFile(path,source);
   await assert.rejects(readProfileFile(path),reason);
  }
  await writeFile(path,Buffer.from([0xc0,0xaf]));
  await assert.rejects(readProfileFile(path));
  await writeFile(path," ".repeat(65537));
  await assert.rejects(readProfileFile(path),/exceeds 64 KiB/);
 }finally{await rm(dir,{recursive:true,force:true})}
});

test("registry loader rejects final-component symlinks and directories",{
 skip:process.platform!=="linux"
},async()=>{
 const dir=await mkdtemp(join(tmpdir(),"ores-registry-special-"));
 try{
  const good=join(dir,"good.json"),sym=join(dir,"link.json");
  await writeFile(good,'{"schemaVersion":1}');
  await symlink(good,sym);
  await assert.rejects(readProfileFile(sym));
  await assert.rejects(readProfileFile(dir),/regular file/);
  const fifo=join(dir,"profile.fifo");
  const mk=spawnSync("mkfifo",[fifo],{encoding:"utf8",timeout:3000});
  assert.equal(mk.status,0,mk.stderr);
  // A subprocess timeout makes a blocking FIFO regression fail boundedly
  // without leaving an uncancelled timer or stuck file read in this test.
  const moduleUrl=new URL("../src/load-registry.mjs",import.meta.url).href;
  const code="import {readProfileFile} from "+JSON.stringify(moduleUrl)+";"+
    "try{await readProfileFile(process.argv[1]);process.exit(3)}"+
    "catch(e){process.exit(/regular file/.test(e.message)?0:4)}";
  const outcome=spawnSync(process.execPath,
    ["--input-type=module","--eval",code,fifo],
    {encoding:"utf8",timeout:3000});
  assert.equal(outcome.status,0,outcome.stderr||String(outcome.error));
 }finally{await rm(dir,{recursive:true,force:true})}
});
