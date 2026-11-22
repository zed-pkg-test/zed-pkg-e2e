// Real attoc -> compiled Wasm guest runtime range checking, not host evaluation.
import assert from 'node:assert/strict';
import {mkdtempSync,readFileSync,writeFileSync,existsSync,rmSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {resolve,join} from 'node:path';
const compiler=process.env.ATTOC||resolve('target/debug/attoc');
assert.ok(existsSync(compiler),'native attoc is required');
const declaration=[
 'define newtype Port(i64) where value gte 1 and value lte 65535; end',
 'fnc create_port(int value): Result<Port,RangeError> {',
 ' return Port.try_from(value);',
 '}'
].join('\n');
const guest=(number,modified='')=>[
 declaration,
 'pub fnc main(): int {',
 ' val int input = '+number+';',
 ' val Result<Port,RangeError> result = create_port(input);',
 ' match result over',
 ' on Ok(port) -> { return port.to_base(); }',
 ' on Err(reason) -> { return 0; }',
 ' end',
 '}',
 modified
].join('\n');
const cases=[
 ['min-admitted','1',1n],['ordinary','42',42n],
 ['max-admitted','65535',65535n],['too-small','0',0n],
 ['negative','-12',0n],['too-large','65536',0n],
 ['high-signed','9223372036854775807',0n],
 ['direct-checked','8080',8080n],
 ['unbounded-min','-9223372036854775808',-9223372036854775808n],
 ['unbounded-max','9223372036854775807',9223372036854775807n],
 ['return-match-valid','8080',8080n],
 ['return-match-low','0',0n],
 ['return-match-high','65536',0n],
 ['return-match-reverse','65535',65535n]
];
const directory=mkdtempSync(join(tmpdir(),'atto-newtype-runtime-'));
const run=(name,source)=>{
 const input=join(directory,name+'.atto');
 const output=join(directory,name+'.wasm');
 writeFileSync(input,source);
 const result=spawnSync(compiler,[input,'-o',output],{
  encoding:'utf8',timeout:30000,maxBuffer:1048576});
 if(result.error) throw result.error;
 return {result,output};
};
try{
 for(const [name,input,want] of cases){
   let program=guest(input);
   if(name==='direct-checked') {
      program=program.replace('create_port(input)','Port.try_from(input)');
   }
   if(name.startsWith('unbounded-')) {
      program=program.replace(' where value gte 1 and value lte 65535; end',';');
   }
   if(name.startsWith('return-match-')) {
      program=program.replace(
        ' match result over\n on Ok(port) -> { return port.to_base(); }\n on Err(reason) -> { return 0; }\n end',
        ' return match result over\n on Ok(port) -> port.to_base(),\n on Err(reason) -> 0\n end;'
      );
   }
   if(name==='return-match-reverse') {
      program=program.replace(
        'on Ok(port) -> port.to_base(),\n on Err(reason) -> 0',
        'on Err(reason) -> 0,\n on Ok(port) -> port.to_base()'
      );
   }
   const {result,output}=run(name,program);
   assert.equal(result.status,0,name+': '+result.stderr);
   const wasm=readFileSync(output);
   assert.ok(WebAssembly.validate(wasm),name);
   const module=new WebAssembly.Module(wasm);
   assert.deepEqual(WebAssembly.Module.imports(module),[],name+': unsupported host validation');
   const instance=new WebAssembly.Instance(module,{});
   assert.equal(instance.exports.main(),want,name+': runtime range branch');
   assert.equal(instance.exports.panic_code.value,0,name+': recoverable Err must not panic');
   assert.ok(instance.exports.memory instanceof WebAssembly.Memory,
      name+': existing tagged Result storage must be a guest allocation');
 }
 const malformed=[
  ['wrong-nominal',guest('42').replace(
    'Result<Port,RangeError> result','Result<Other,RangeError> result')],
  ['unsafe-cast',guest('42').replace('create_port(input)','input')],
  ['wrong-err',guest('42').replace('Result<Port,RangeError> result',
    'Result<Port,string> result')],
  ['incorrect-match',guest('42').replace('on Err(reason)','on Ok(reason)')],
  ['return-match-not-exhaustive',guest('42').replace(
    ' match result over\n on Ok(port) -> { return port.to_base(); }\n on Err(reason) -> { return 0; }\n end',
    ' return match result over\n on Ok(port) -> port.to_base(),\n on Ok(again) -> 0\n end;')],
  ['return-match-implicit-opaque',guest('42').replace(
    ' match result over\n on Ok(port) -> { return port.to_base(); }\n on Err(reason) -> { return 0; }\n end',
    ' return match result over\n on Ok(port) -> port,\n on Err(reason) -> 0\n end;')],
  ['return-match-missing-comma',guest('42').replace(
    ' match result over\n on Ok(port) -> { return port.to_base(); }\n on Err(reason) -> { return 0; }\n end',
    ' return match result over\n on Ok(port) -> port.to_base()\n on Err(reason) -> 0\n end;')],
 ];
 for(const [name,source] of malformed){
    const {result,output}=run(name,source);
    assert.notEqual(result.status,0,name+': invalid guest was compiled');
    assert.equal(existsSync(output),false,name+': rejected compilation emitted artifact');
 }
 console.log('PASS 14 real compiled runtime-newtype Wasm guests (9 valid/5 Err), 7 invalid/no-artifact checks, zero host imports');
}finally{
 rmSync(directory,{recursive:true,force:true});
}
