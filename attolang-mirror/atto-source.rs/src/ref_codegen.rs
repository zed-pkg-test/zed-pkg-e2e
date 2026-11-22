//! Cranelift lowering proof for Oreslang compressed managed references.
//!
//! These helpers operate only on non-null references whose domain, lifetime,
//! alignment and bounds have already been validated by MIR/runtime checks.
use cranelift::{
    codegen::{
        ir::{types, AbiParam},
        settings,
    },
    frontend::{FunctionBuilder, FunctionBuilderContext},
    module::{FuncId, Linkage, Module as ClifModule},
    native,
    object::{ObjectBuilder, ObjectModule},
    prelude::InstBuilder,
};

const DECODE_SYMBOL: &str = "__ores_ref_decode_nonnull";
const ENCODE_SYMBOL: &str = "__ores_ref_encode_nonnull";

pub fn emit_codec_object(shift: u8) -> Result<Vec<u8>, String> {
    if shift > 15 {
        return Err("compressed reference alignment shift exceeds 15".into());
    }

    let isa = native::builder()
        .map_err(|e| e.to_string())?
        .finish(settings::Flags::new(settings::builder()))
        .map_err(|e| e.to_string())?;
    let obj_builder = ObjectBuilder::new(
        isa,
        "oreslang-ref-codec",
        cranelift::module::default_libcall_names(),
    )
    .map_err(|e| e.to_string())?;
    let mut module = ObjectModule::new(obj_builder);

    let mut decode_signature = module.make_signature();
    decode_signature.params.push(AbiParam::new(types::I64));
    decode_signature.params.push(AbiParam::new(types::I32));
    decode_signature.returns.push(AbiParam::new(types::I64));
    let decode_id = module
        .declare_function(DECODE_SYMBOL, Linkage::Export, &decode_signature)
        .map_err(|e| e.to_string())?;

    let mut encode_signature = module.make_signature();
    encode_signature.params.push(AbiParam::new(types::I64));
    encode_signature.returns.push(AbiParam::new(types::I32));
    let encode_id = module
        .declare_function(ENCODE_SYMBOL, Linkage::Export, &encode_signature)
        .map_err(|e| e.to_string())?;

    define_decode(&mut module, decode_id, shift)?;
    define_encode(&mut module, encode_id, shift)?;
    module.finish().emit().map_err(|e| e.to_string())
}

fn define_decode(module: &mut ObjectModule, id: FuncId, shift: u8) -> Result<(), String> {
    let mut ctx = module.make_context();
    ctx.func.signature.params.push(AbiParam::new(types::I64));
    ctx.func.signature.params.push(AbiParam::new(types::I32));
    ctx.func.signature.returns.push(AbiParam::new(types::I64));

    let frontend_config = module.isa().frontend_config();
    let mut fbctx = FunctionBuilderContext::new();
    {
        let mut builder = FunctionBuilder::new(&mut ctx.func, &mut fbctx);
        let entry = builder.create_block();
        builder.append_block_params_for_function_params(entry);
        builder.switch_to_block(entry);
        builder.seal_block(entry);

        let params = builder.block_params(entry);
        let base = params[0];
        let stored = params[1];
        let wide = builder.ins().uextend(types::I64, stored);
        let one = builder.ins().iconst(types::I64, 1);
        let slot = builder.ins().isub(wide, one);
        let offset = builder.ins().ishl_imm_u(slot, i64::from(shift));
        let address = builder.ins().iadd(base, offset);
        builder.ins().return_(&[address]);
        builder.finalize(frontend_config);
    }

    module
        .define_function(id, &mut ctx)
        .map_err(|e| e.to_string())?;
    module.clear_context(&mut ctx);
    Ok(())
}

fn define_encode(module: &mut ObjectModule, id: FuncId, shift: u8) -> Result<(), String> {
    let mut ctx = module.make_context();
    ctx.func.signature.params.push(AbiParam::new(types::I64));
    ctx.func.signature.returns.push(AbiParam::new(types::I32));

    let frontend_config = module.isa().frontend_config();
    let mut fbctx = FunctionBuilderContext::new();
    {
        let mut builder = FunctionBuilder::new(&mut ctx.func, &mut fbctx);
        let entry = builder.create_block();
        builder.append_block_params_for_function_params(entry);
        builder.switch_to_block(entry);
        builder.seal_block(entry);

        let offset = builder.block_params(entry)[0];
        let slot = builder.ins().ushr_imm_u(offset, i64::from(shift));
        let one = builder.ins().iconst(types::I64, 1);
        let stored_wide = builder.ins().iadd(slot, one);
        let stored = builder.ins().ireduce(types::I32, stored_wide);
        builder.ins().return_(&[stored]);
        builder.finalize(frontend_config);
    }

    module
        .define_function(id, &mut ctx)
        .map_err(|e| e.to_string())?;
    module.clear_context(&mut ctx);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn emits_native_codec_object() {
        let object = emit_codec_object(3).unwrap();
        assert!(!object.is_empty());
        #[cfg(target_os = "linux")]
        assert!(object.starts_with(b"\x7fELF"));
    }

    #[test]
    fn rejects_excessive_alignment_shift() {
        assert!(emit_codec_object(16).is_err());
    }
}
