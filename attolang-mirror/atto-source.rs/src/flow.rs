//! Checked scalar HIR -> acyclic CFG safety-contract adapter.
//! Only the admitted scalar subset is complete. Unsupported calls must never
//! quietly become no-op effects. The frontend cannot yet emit actor/Task MIR.
use crate::{frontend::Expr, mir::Module};
use serde_json::{json, Value};

fn emit_expr(expr: &Expr, events: &mut Vec<Value>) -> Result<(), String> {
    match expr {
        Expr::Integer(_) => events.push(json!({"kind":"compute","units":1})),
        Expr::Name(name) => {
            events.push(json!({"kind":"read","place":name}));
            events.push(json!({"kind":"compute","units":1}));
        }
        Expr::Negate(value) => {
            emit_expr(value, events)?;
            events.push(json!({"kind":"compute","units":1}));
        }
        Expr::Add(a, b) | Expr::Subtract(a, b) | Expr::Multiply(a, b) => {
            emit_expr(a, events)?;
            emit_expr(b, events)?;
            events.push(json!({"kind":"compute","units":1}));
        }
        Expr::Call { name, .. } => {
            return Err(format!(
                "unsupported CFG interprocedural effect summary: {name}"
            ));
        }
    }
    Ok(())
}

/// Emit the analyzer's CFG-envelope v1, with a caller-specified budget in
/// abstract operations (NOT real time, nanoseconds, scheduler ticks or WCET).
///
/// This producer deliberately refuses calls and all expressions outside the
/// currently statically checked scalar AST. It does not infer actor safety from
/// Cranelift code or attest that future syntax is modeled.
pub fn export_scalar_flow(module: &Module, max_uninterrupted_units: u64) -> Result<Value, String> {
    let mut functions = Vec::with_capacity(module.functions.len());
    for function in &module.functions {
        let mut ops = Vec::new();
        for param in &function.params {
            ops.push(json!({"kind":"declare","place":param,"domain":"local"}));
        }
        for (name, value) in &function.locals {
            emit_expr(value, &mut ops)?;
            ops.push(json!({"kind":"declare","place":name,"domain":"local"}));
        }
        emit_expr(&function.return_value, &mut ops)?;
        functions.push(json!({
            "name":function.symbol,
            "entry":"entry",
            "max_uninterrupted_units":max_uninterrupted_units,
            "blocks":[{
                "id":"entry","ops":ops,
                "terminator":{"kind":"return"}
            }]
        }));
    }
    Ok(json!({"schema_version":1,"effects_complete":true,"functions":functions}))
}
#[cfg(test)]
mod tests {
    use super::*;
    fn lower(source: &str) -> Module {
        crate::mir::lower(crate::frontend::parse(source).unwrap()).unwrap()
    }
    #[test]
    fn tracks_local_ownership_and_reads() {
        let module = lower("fnc f(int x): int { const local: int = x+1; return local*2; }");
        let result = export_scalar_flow(&module, 6).unwrap();
        let operations = result["functions"][0]["blocks"][0]["ops"]
            .as_array()
            .unwrap();
        assert_eq!(
            operations[0],
            json!({"kind":"declare","place":"x","domain":"local"})
        );
        assert!(operations
            .iter()
            .any(|op| op == &json!({"kind":"read","place":"local"})));
        assert_eq!(
            operations
                .iter()
                .filter(|op| op["kind"] == "compute")
                .count(),
            6
        );
        assert_eq!(result["effects_complete"], true);
    }
    #[test]
    fn refuses_calls_until_interprocedural_summaries_exist() {
        let module = lower(
            "pub fnc main(): int { return add(40,2); } fnc add(int a,int b): int { return a+b; }",
        );
        assert!(export_scalar_flow(&module, 50).unwrap_err().contains("add"));
    }
    #[test]
    fn exported_cfg_has_explicit_return() {
        let module = lower("fnc f(): int { return 42; }");
        let result = export_scalar_flow(&module, 1).unwrap();
        assert_eq!(
            result["functions"][0]["blocks"][0]["terminator"],
            json!({"kind":"return"})
        );
    }
}
