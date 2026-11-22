//! Backend-neutral effect-IR v1 adapter for oreslang-static-analysis.rs.
//! A complete semantic/MIR pass must cover every operation before export.
use crate::{frontend::Expr, mir::Module};
use serde_json::{json, Value};

fn analyze_expr(expr: &Expr, out: &mut Vec<Value>) {
    match expr {
        Expr::Integer(_) | Expr::Name(_) => {
            out.push(json!({"kind":"compute", "units": 1}));
        }
        Expr::Negate(v) => {
            analyze_expr(v, out);
            out.push(json!({"kind":"compute", "units": 1}));
        }
        Expr::Add(a, b) | Expr::Subtract(a, b) | Expr::Multiply(a, b) => {
            analyze_expr(a, out);
            analyze_expr(b, out);
            out.push(json!({"kind":"compute", "units": 1}));
        }
        Expr::Call { name, args } => {
            for arg in args {
                analyze_expr(arg, out);
            }
            out.push(json!({"kind":"call", "target":name}));
        }
    }
}

/// Validate abstract resource budgets without recursing into attacker-shaped
/// expressions. The analyzer itself admits at most 100,000 operation records.
const MAX_EFFECT_OPERATIONS: usize = 100_000;
const MAX_EFFECT_EXPR_DEPTH: usize = 64;
const MAX_EFFECT_FUNCTIONS: usize = 4096;

/// Checked export path required by CLI and paired compilation. The existing
/// low-level exporter remains available for compatibility but MUST NOT be
/// treated as a soundness/security boundary on its own.
pub fn export_effect_ir_checked(module: &Module) -> Result<Value, String> {
    if module.functions.is_empty() || module.functions.len() > MAX_EFFECT_FUNCTIONS {
        return Err("effect IR function budget exceeded or module empty".into());
    }
    let mut count = 0usize;
    for function in &module.functions {
        let mut pending: Vec<(&Expr, usize)> = function
            .locals
            .iter()
            .map(|(_, expression)| (expression, 1))
            .collect();
        pending.push((&function.return_value, 1));
        while let Some((expr, depth)) = pending.pop() {
            if depth > MAX_EFFECT_EXPR_DEPTH || count >= MAX_EFFECT_OPERATIONS {
                return Err("effect IR expression depth/operation budget exceeded".into());
            }
            count += 1;
            match expr {
                Expr::Integer(_) | Expr::Name(_) => {}
                Expr::Negate(v) => pending.push((v, depth + 1)),
                Expr::Add(a, b) | Expr::Subtract(a, b) | Expr::Multiply(a, b) => {
                    pending.push((a, depth + 1));
                    pending.push((b, depth + 1));
                }
                Expr::Call { args, .. } => {
                    pending.extend(args.iter().map(|arg| (arg, depth + 1)));
                }
            }
        }
    }
    Ok(export_effect_ir(module))
}

/// This is only complete for the admitted scalar subset: no allocation, FFI,
/// unbounded waits, dynamic calls, or actor mutation operations exist in it.
/// Abstract compute units are NOT CPU instructions, WCET or timing guarantees.
pub fn export_effect_ir(module: &Module) -> Value {
    let functions: Vec<_> = module
        .functions
        .iter()
        .map(|func| {
            let mut operations = Vec::new();
            for (_, expression) in &func.locals {
                analyze_expr(expression, &mut operations);
            }
            analyze_expr(&func.return_value, &mut operations);
            json!({"name": func.symbol, "operations": operations})
        })
        .collect();
    json!({
        "schema_version": 1,
        "functions": functions
    })
}

/// Optional layout metadata envelope; the nested canonical effect-IR v1
/// remains byte/schema compatible with mainline analyzer golden fixtures.
/// This describes internal representation only, not runtime/borrow authority.
pub fn export_effect_ir_with_layout_checked(module: &Module) -> Result<Value, String> {
    let effect_ir = export_effect_ir_checked(module)?;
    Ok(json!({
        "schema": "oreslang.managed-ref-effect-envelope.v1",
        "target": {
            "managed_reference_mode": module.managed_refs.mode_name(),
            "managed_reference_storage_bits": module.managed_refs.storage_bits(),
            "managed_reference_alignment_shift": module.managed_refs.alignment_shift(),
        },
        "effect_ir": effect_ir
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn checked_forward_effects_match_independent_analyzer_fixture() {
        let source = include_str!("../tests/fixtures/forward.ores");
        let expected: Value =
            serde_json::from_str(include_str!("../tests/fixtures/forward.effects.json")).unwrap();
        let checked = crate::mir::lower(crate::frontend::parse(source).unwrap()).unwrap();
        let actual = export_effect_ir_checked(&checked).unwrap();
        assert_eq!(actual, expected);
    }
    #[test]
    fn checked_scalar_effects_have_exact_work_count() {
        let ast = crate::frontend::parse("pub fnc main(): int { return 40+2; }").unwrap();
        let module = crate::mir::lower(ast).unwrap();
        let ir = export_effect_ir_checked(&module).unwrap();
        let operations = ir["functions"][0]["operations"].as_array().unwrap();
        assert_eq!(operations.len(), 3);
        assert!(operations
            .iter()
            .all(|op| op["kind"] == "compute" && op["units"] == 1));
    }
    #[test]
    fn checked_effects_reject_adversarial_ast_depth() {
        let mut expression = Expr::Integer(1);
        for _ in 0..70 {
            expression = Expr::Negate(Box::new(expression));
        }
        let module = Module {
            managed_refs: crate::ref_layout::ManagedRefLayout::default(),
            exports: vec![],
            functions: vec![crate::mir::Function {
                symbol: "x".into(),
                exported: false,
                params: vec![],
                locals: vec![],
                return_value: expression,
            }],
        };
        assert!(export_effect_ir_checked(&module).is_err());
    }
    #[test]
    fn tracks_forward_calls() {
        let ast = crate::frontend::parse(
            "pub fnc main(): int { return add(40,2); }\nfnc add(int a,int b): int { return a+b; }",
        )
        .unwrap();
        let mir = crate::mir::lower(ast).unwrap();
        let ir = export_effect_ir(&mir);
        assert_eq!(ir["functions"][0]["operations"][2]["kind"], "call");
        assert_eq!(ir["functions"][0]["operations"][2]["target"], "add");
    }
    #[test]
    fn layout_envelope_preserves_canonical_effect_v1_fixture() {
        let source = include_str!("../tests/fixtures/forward.ores");
        let expected: Value =
            serde_json::from_str(include_str!("../tests/fixtures/forward.effects.json")).unwrap();
        for layout in [
            crate::ref_layout::ManagedRefLayout::default(),
            crate::ref_layout::ManagedRefLayout::native(),
        ] {
            let module =
                crate::mir::lower_with_ref_layout(crate::frontend::parse(source).unwrap(), layout)
                    .unwrap();
            let envelope = export_effect_ir_with_layout_checked(&module).unwrap();
            assert_eq!(envelope["effect_ir"], expected);
            assert_eq!(
                envelope["target"]["managed_reference_mode"],
                layout.mode_name()
            );
            assert_eq!(
                envelope["target"]["managed_reference_storage_bits"],
                layout.storage_bits()
            );
            assert_eq!(export_effect_ir_checked(&module).unwrap(), expected);
        }
    }
}
