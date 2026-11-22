// Compile native AttoLang Rust/browser compiler to actual WebAssembly guests.
// No host-side newtype simulation or guest interpreter.
import assert from 'node:assert/strict';
import {mkdtempSync,writeFileSync,readFileSync,existsSync,rmSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
const attoc=process.env.ATTOC||resolve('target/debug/attoc');
assert.ok(existsSync(attoc),'real native attoc required');
const definition=[
 'define newtype Port(i64) where value gte 1 and value lte 65535; end',
 'fnc identity(Port x): Port { return x; }',
 'fnc unbox(Port x): int { return x.to_base(); }'
].join('\n')+'\n';
const cases=[
 ['lower-bound',1n,'Port.from_const(1)'],
 ['middle',42n,'Port.from_const(42)'],
 ['upper-bound',65535n,'Port.from_const(65535)']
];
const failures=[
 ['below-range','Port.from_const(0)','E_RANGE_INVALID'],
 ['above-range','Port.from_const(65536)','E_RANGE_INVALID'],
 ['unproved-runtime','Port.try_from(42)','E_UNSUPPORTED']
];
const dir=mkdtempSync(join(tmpdir(),'atto-opaque-guest-'));
function build(name,expr){
 const input=join(dir,name+'.atto'),output=join(dir,name+'.wasm');
 const source=definition+[
  'pub fnc main(): int {',
  ' val Port port = '+expr+';',
  ' return unbox(identity(port));',
  '}'
 ].join('\n');
 writeFileSync(input,source);
 const result=spawnSync(attoc,[input,'-o',output],{encoding:'utf8',timeout:30000});
 if(result.error) throw result.error;
 return {result,output};
}
try{
 for(const [name,want,expr] of cases){
   const {result,output}=build(name,expr);
   assert.equal(result.status,0,name+': '+result.stderr);
   const bytes=readFileSync(output);
   assert.ok(WebAssembly.validate(bytes),name);
   const module=new WebAssembly.Module(bytes);
   assert.deepEqual(WebAssembly.Module.imports(module),[],name+': unexpected host imports');
   const instance=new WebAssembly.Instance(module,{});
   assert.equal(instance.exports.main(),want,name);
   assert.equal(instance.exports.panic_code.value,0,name);
   assert.equal(instance.exports.memory,undefined,name+': scalar newtype must not allocate guest heap');
 }
 for(const [name,expr,error] of failures){
   const {result,output}=build(name,expr);
   assert.notEqual(result.status,0,name);
   assert.ok(result.stderr.includes(error),name+': '+result.stderr);
   assert.equal(existsSync(output),false,name+': rejected code generated a guest');
 }
 console.log('PASS 3 compiled Wasm nominal guests, 3 rejected/no-artifact cases; 0 imports; 0 guest heap');
}finally{rmSync(dir,{recursive:true,force:true});}
