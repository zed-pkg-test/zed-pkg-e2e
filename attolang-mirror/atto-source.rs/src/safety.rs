//! Source-to-safety-IR adapter for the statically checked scalar-only MIR.
//! Explicitly fails closed for calls. No hard-real-time certificate is issued.
use crate::{frontend::Expr, mir::Module};
use serde_json::{json, Value};

fn emit_expr(expr: &Expr, events: &mut Vec<Value>) {
    match expr {
        Expr::Integer(_) => events.push(json!({"kind":"compute","units":1})),
        Expr::Name(name) => events.push(json!({"kind":"read","place":name})),
        Expr::Negate(value) => {
            emit_expr(value, events);
            events.push(json!({"kind":"compute","units":1}));
        }
        Expr::Add(a, b) | Expr::Subtract(a, b) | Expr::Multiply(a, b) => {
            emit_expr(a, events);
            emit_expr(b, events);
            events.push(json!({"kind":"compute","units":1}));
        }
        Expr::Call { name, args } => {
            for value in args {
                emit_expr(value, events);
            }
            // Must remain a proof gap until call summaries compose.
            events.push(json!({"kind":"call","target":name}));
        }
    }
}

/// Source-language semantic checking must succeed before invoking this exporter.
/// A limit is a policy input, not a compiler-inferred WCET value.
pub fn export_safety_ir(module: &Module, max_uninterrupted_units: Option<u64>) -> Value {
    let functions: Vec<_> = module
        .functions
        .iter()
        .map(|function| {
            let mut events = Vec::new();
            for name in &function.params {
                events.push(json!({"kind":"define","place":name}));
            }
            for (name, expr) in &function.locals {
                emit_expr(expr, &mut events);
                events.push(json!({"kind":"define","place":name}));
            }
            emit_expr(&function.return_value, &mut events);
            json!({
                "name": function.symbol,
                "control_flow": "linear",
                "max_uninterrupted_units": max_uninterrupted_units,
                "allow_error_propagation": false,
                "events": events
            })
        })
        .collect();
    json!({
        "schema_version": 1,
        // Validated for this scalar MIR grammar only. This is not a proof of
        // runtime stack use, scheduler cooperation, target WCET or memory safety.
        "effects_complete": true,
        "functions": functions
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    fn render(text: &str, budget: Option<u64>) -> Value {
        let ast = crate::frontend::parse(text).unwrap();
        let mir = crate::mir::lower(ast).unwrap();
        export_safety_ir(&mir, budget)
    }
    #[test]
    fn pure_scalar_yields_bounded_compute_events() {
        let ir = render("pub fnc answer(): int { return 40 + 2; }", Some(16));
        assert_eq!(ir["schema_version"], 1);
        assert_eq!(ir["functions"][0]["events"][0]["kind"], "compute");
        assert_eq!(ir["functions"][0]["max_uninterrupted_units"], 16);
    }
    #[test]
    fn calls_cannot_disappear_from_safety_ir() {
        let ir = render(
            "pub fnc main(): int { return add(1,2); } fnc add(int a,int b): int { return a+b; }",
            None,
        );
        let ops = ir["functions"][0]["events"].as_array().unwrap();
        assert!(ops
            .iter()
            .any(|v| v["kind"] == "call" && v["target"] == "add"));
    }
    #[test]
    fn local_reads_are_explicit() {
        let ir = render(
            "pub fnc f(int x): int { const y: int = x; return y; }",
            Some(16),
        );
        let events = ir["functions"][0]["events"].as_array().unwrap();
        assert!(events
            .iter()
            .any(|v| v["kind"] == "read" && v["place"] == "x"));
        assert!(events
            .iter()
            .any(|v| v["kind"] == "read" && v["place"] == "y"));
    }
}
