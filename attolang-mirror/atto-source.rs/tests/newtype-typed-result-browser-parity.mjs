// Actual native attoc vs wasm32-unknown-unknown compiler ABI: compile exactly
// identical typed nominal Result source, compare guest bytes and run both.
// No JS semantic simulation, host runtime constructor checks, JIT or fallback.
import assert from 'node:assert/strict';
import {readFileSync,writeFileSync,mkdtempSync,existsSync,rmSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {tmpdir} from 'node:os';

const compilerPath = process.env.ATTO_WASM32_COMPILER ||
 fileURLToPath(new URL('../target/wasm32-unknown-unknown/release/atto_compiler.wasm',import.meta.url));
const nativePath = process.env.ATTOC ||
 fileURLToPath(new URL('../target/debug/attoc',import.meta.url));
const bytes = readFileSync(compilerPath);
assert.ok(WebAssembly.validate(bytes), 'actual browser compiler module is required');
const module = new WebAssembly.Module(bytes);
assert.deepEqual(WebAssembly.Module.imports(module),[], 'browser compiler has no host imports');
const {exports:api}=new WebAssembly.Instance(module,{});
assert.equal(api.abi_version(),2,'browser compiler ABI must match');
const encoder=new TextEncoder();
const decoder=new TextDecoder('utf8',{fatal:true});
function browser(source){
 const data=encoder.encode(source);
 assert.ok(data.byteLength>0&&data.byteLength<=api.input_capacity());
 new Uint8Array(api.memory.buffer,api.input_ptr(),data.length).set(data);
 const status=api.compile(data.length);
 if(status!==0){
   assert.equal(api.result_length(),0,'failed browser compiler returned guest bytes');
   const view=new Uint8Array(api.memory.buffer);
   const start=api.error_ptr();
   let end=start;
   while(end<start+512&&view[end]!==0)end++;
   return {ok:false,diagnostic:decoder.decode(view.subarray(start,end))};
 }
 const result=new Uint8Array(api.memory.buffer,api.result_ptr(),api.result_length()).slice();
 assert.ok(WebAssembly.validate(result));
 return {ok:true,bytes:Buffer.from(result)};
}
const dir=mkdtempSync(join(tmpdir(),'atto-typed-result-parity-'));
function native(src,name){
 const input=join(dir,name+'.atto');
 const out=join(dir,name+'.wasm');
 writeFileSync(input,src);
 const child=spawnSync(nativePath,[input,'-o',out],{encoding:'utf8',timeout:30000});
 if(child.error)throw child.error;
 return {status:child.status,stderr:child.stderr,output:out};
}
const program=value=>[
 'define newtype Port(i64) where value gte 1 and value lte 65535; end',
 'fnc create_port(int value): Result<Port,RangeError> { return Port.try_from(value); }',
 'pub fnc main(): int {',
 ' val int input = '+value+';',
 ' val Result<Port,RangeError> result = create_port(input);',
 ' return match result over',
 '   on Ok(port) -> port.to_base(),',
 '   on Err(reason) -> 0',
 ' end;',
 '}'
].join('\n');
let passed=0;
try {
 const cases=[
  ['lower-bound','1',1n],['middle','8080',8080n],['upper-bound','65535',65535n],
  ['range-zero','0',0n],['range-over','65536',0n],
  ['range-negative','-9223372036854775808',0n]
 ];
 for(const [name,value,expected] of cases) {
   const source=program(value);
   const compiled=browser(source);
   assert.ok(compiled.ok,name+': '+compiled.diagnostic);
   const n=native(source,name);
   assert.equal(n.status,0,name+': '+n.stderr);
   const local=readFileSync(n.output);
   assert.deepEqual(compiled.bytes,local,name+': native vs wasm32 guest byte parity');
   const guest=new WebAssembly.Module(compiled.bytes);
   assert.deepEqual(WebAssembly.Module.imports(guest),[],name+': guest host imports');
   const instance=new WebAssembly.Instance(guest,{});
   assert.equal(instance.exports.main(),expected,name+': compiled runtime check result');
   assert.equal(instance.exports.panic_code.value,0,name+': recoverable Err is not panic');
   passed++;
 }
 const negative=[
  ['wrong-error-type',program('42').replace('Result<Port,RangeError> result','Result<Port,Error> result')],
  ['unproven-implicit',program('42').replace('create_port(input);','input;')],
  ['wrong-scope-type',program('42').replace('Result<Port,RangeError> result','Result<Wrong,RangeError> result')],
  ['non-exhaustive',program('42').replace('on Err(reason) -> 0','on Ok(repeated) -> 0')],
  ['implicit-unwrapping',program('42').replace('port.to_base(),','port,')],
  ['missing-arm-comma',program('42').replace('port.to_base(),','port.to_base()')],
  ['public-nominal-escape',program('42').replace('define newtype Port','define pub newtype Port')],
  ['public-result-escape',program('42').replace('fnc create_port','pub fnc create_port')],
 ];
 for(const [name,source] of negative){
   const compiled=browser(source);
   const n=native(source,name);
   assert.equal(compiled.ok,false,name+': browser unexpectedly compiled');
   assert.notEqual(n.status,0,name+': native unexpectedly compiled');
   assert.ok(!existsSync(n.output),name+': failed native source emitted Wasm artifact');
   const m=compiled.diagnostic.match(/E_[A-Z_]+/);
   assert.ok(m,name+': missing browser error code '+compiled.diagnostic);
   assert.ok(n.stderr.includes(m[0]),name+': mismatched browser/native diagnostic '+n.stderr);
 }
 console.log('PASS '+passed+' native/wasm32 browser compiler byte-identical typed Result guests; 8 matching diagnostic/no-artifact negatives; zero imports');
} finally {
 rmSync(dir,{recursive:true,force:true});
}
