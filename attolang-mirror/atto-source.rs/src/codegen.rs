//! Cranelift native-object backend. Oreslang semantic admission precedes this pass.
use crate::{frontend::Expr, mir::Module};
use cranelift::{
    codegen::{
        ir::{types, AbiParam, FuncRef, TrapCode, Value},
        settings,
    },
    frontend::{FunctionBuilder, FunctionBuilderContext},
    module::{FuncId, Linkage, Module as ClifModule},
    native,
    object::{ObjectBuilder, ObjectModule},
    prelude::InstBuilder,
};
use std::collections::HashMap;

// The baseline scalar compiler retains wrapping arithmetic until effect IR
// can encode conditional integer-overflow failure edges.
// FULL_GUEST_AOT_TODO (platform-conformance #321): This Cranelift object
// emitter compiles its currently admitted scalar MIR, not arbitrary AttoLang
// classes, actors, generic specializations or suspendable continuations.
// Extend the typed closed-world graph before widening strict native AOT;
// unknown dispatch and host runtime callbacks must reject, never interpret.
// ZERO_TRACING_GC_TODO: No tracing collector is needed for this scalar slice,
// but allocator-free functions are not proof of an entirely zero-GC process.
// Future actor-owned heaps need compressed 32-bit region-relative handles,
// identity/generation checks and deterministic unwind/cancellation/drop,
// with linked native ABI/FFI and worker-host allocation evidence.
pub fn emit_object(program: &Module) -> Result<Vec<u8>, String> {
    emit_object_with_arithmetic_mode(program, false)
}

/// Opt-in native signed i64 overflow checks; no typed guest Error contract.
pub fn emit_object_checked_i64(program: &Module) -> Result<Vec<u8>, String> {
    emit_object_with_arithmetic_mode(program, true)
}

fn emit_object_with_arithmetic_mode(
    program: &Module,
    checked_i64: bool,
) -> Result<Vec<u8>, String> {
    let isa = native::builder()
        .map_err(|e| e.to_string())?
        .finish(settings::Flags::new(settings::builder()))
        .map_err(|e| e.to_string())?;
    let obj_builder = ObjectBuilder::new(
        isa,
        "oreslang-module",
        cranelift::module::default_libcall_names(),
    )
    .map_err(|e| e.to_string())?;
    let mut module = ObjectModule::new(obj_builder);

    // Declare every function before compiling any bodies: forward calls work.
    let mut ids = HashMap::<String, FuncId>::new();
    for function in &program.functions {
        let mut signature = module.make_signature();
        for _ in &function.params {
            signature.params.push(AbiParam::new(types::I64));
        }
        signature.returns.push(AbiParam::new(types::I64));
        // Preserve source-level visibility in the native ABI: private helpers
        // remain callable internally, but cannot be linked from another object.
        // Keep the existing implicit executable entry-point ABI for "main",
        // which is also a root in the release tree-shaker.
        let linkage = if function.exported || function.symbol == "main" {
            Linkage::Export
        } else {
            Linkage::Local
        };
        let id = module
            .declare_function(&function.symbol, linkage, &signature)
            .map_err(|e| e.to_string())?;
        ids.insert(function.symbol.clone(), id);
    }

    for function in &program.functions {
        let function_id = *ids
            .get(&function.symbol)
            .ok_or_else(|| format!("missing function: {}", function.symbol))?;
        let mut ctx = module.make_context();
        for _ in &function.params {
            ctx.func.signature.params.push(AbiParam::new(types::I64));
        }
        ctx.func.signature.returns.push(AbiParam::new(types::I64));
        let frontend_config = module.isa().frontend_config();
        let mut calls = HashMap::<String, FuncRef>::new();
        for (name, id) in &ids {
            let reference = module.declare_func_in_func(*id, &mut ctx.func);
            calls.insert(name.clone(), reference);
        }

        let mut fbctx = FunctionBuilderContext::new();
        {
            let mut builder = FunctionBuilder::new(&mut ctx.func, &mut fbctx);
            let entry = builder.create_block();
            builder.append_block_params_for_function_params(entry);
            builder.switch_to_block(entry);
            builder.seal_block(entry);
            let params = builder.block_params(entry).to_vec();
            let mut values = HashMap::new();
            for (name, value) in function.params.iter().zip(params) {
                values.insert(name.clone(), value);
            }
            for (name, expression) in &function.locals {
                let value = compile_expr(&mut builder, expression, &values, &calls, checked_i64)?;
                values.insert(name.clone(), value);
            }
            let result = compile_expr(
                &mut builder,
                &function.return_value,
                &values,
                &calls,
                checked_i64,
            )?;
            builder.ins().return_(&[result]);
            builder.finalize(frontend_config);
        }
        module
            .define_function(function_id, &mut ctx)
            .map_err(|e| e.to_string())?;
        module.clear_context(&mut ctx);
    }
    module.finish().emit().map_err(|e| e.to_string())
}
fn compile_expr(
    builder: &mut FunctionBuilder<'_>,
    expr: &Expr,
    vars: &HashMap<String, Value>,
    calls: &HashMap<String, FuncRef>,
    checked_i64: bool,
) -> Result<Value, String> {
    match expr {
        Expr::Integer(value) => Ok(builder.ins().iconst(types::I64, *value)),
        Expr::Name(name) => vars
            .get(name)
            .copied()
            .ok_or_else(|| format!("unvalidated scalar name: {name}")),
        Expr::Call { name, args } => {
            let callee = *calls
                .get(name)
                .ok_or_else(|| format!("unvalidated function call: {name}"))?;
            let mut operands = Vec::with_capacity(args.len());
            for arg in args {
                operands.push(compile_expr(builder, arg, vars, calls, checked_i64)?);
            }
            let instruction = builder.ins().call(callee, &operands);
            Ok(builder.inst_results(instruction)[0])
        }
        Expr::Add(a, b) => {
            let lhs = compile_expr(builder, a, vars, calls, checked_i64)?;
            let rhs = compile_expr(builder, b, vars, calls, checked_i64)?;
            if checked_i64 {
                let (value, overflow) = builder.ins().sadd_overflow(lhs, rhs);
                builder.ins().trapnz(overflow, TrapCode::INTEGER_OVERFLOW);
                Ok(value)
            } else {
                Ok(builder.ins().iadd(lhs, rhs))
            }
        }
        Expr::Subtract(a, b) => {
            let lhs = compile_expr(builder, a, vars, calls, checked_i64)?;
            let rhs = compile_expr(builder, b, vars, calls, checked_i64)?;
            if checked_i64 {
                let (value, overflow) = builder.ins().ssub_overflow(lhs, rhs);
                builder.ins().trapnz(overflow, TrapCode::INTEGER_OVERFLOW);
                Ok(value)
            } else {
                Ok(builder.ins().isub(lhs, rhs))
            }
        }
        Expr::Multiply(a, b) => {
            let lhs = compile_expr(builder, a, vars, calls, checked_i64)?;
            let rhs = compile_expr(builder, b, vars, calls, checked_i64)?;
            if checked_i64 {
                let (value, overflow) = builder.ins().smul_overflow(lhs, rhs);
                builder.ins().trapnz(overflow, TrapCode::INTEGER_OVERFLOW);
                Ok(value)
            } else {
                Ok(builder.ins().imul(lhs, rhs))
            }
        }
        Expr::Negate(a) => {
            let value = compile_expr(builder, a, vars, calls, checked_i64)?;
            if checked_i64 {
                let zero = builder.ins().iconst(types::I64, 0);
                let (result, overflow) = builder.ins().ssub_overflow(zero, value);
                builder.ins().trapnz(overflow, TrapCode::INTEGER_OVERFLOW);
                Ok(result)
            } else {
                Ok(builder.ins().ineg(value))
            }
        }
    }
}
