//! CFG-v2 exporter for the compiler's checked scalar MIR subset.
//! Only a single explicit return block is currently generated. This is
//! compatible with the CFG verifier, but NOT a full control-flow lowering.
use crate::{frontend::Expr, mir::Module};
use serde_json::{json, Value};

const MAX_FUNCTIONS: usize = 256;
const MAX_NODES_PER_FUNCTION: usize = 4096;
const MAX_OUTPUT_BYTES: usize = 1_048_576;

fn valid_identifier(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 128
        && s.as_bytes()
            .iter()
            .enumerate()
            .all(|(i, b)| b.is_ascii_alphabetic() || *b == b'_' || (i > 0 && b.is_ascii_digit()))
}

/// Independently validate public caller-supplied MIR before emitting a complete
/// effect manifest. Merely being stored in a Module does NOT prove provenance.
fn validate(module: &Module) -> Result<(), String> {
    use std::collections::{HashMap, HashSet};
    if module.functions.is_empty() || module.functions.len() > MAX_FUNCTIONS {
        return Err("empty or oversized MIR module".into());
    }
    let mut signatures = HashMap::new();
    for function in &module.functions {
        if !valid_identifier(&function.symbol)
            || function.params.len() > 64
            || function.locals.len() > MAX_NODES_PER_FUNCTION
            || signatures
                .insert(function.symbol.as_str(), function.params.len())
                .is_some()
        {
            return Err(format!(
                "invalid, duplicated, or oversized MIR function '{}'",
                function.symbol
            ));
        }
    }
    for function in &module.functions {
        let mut scope = HashSet::new();
        for param in &function.params {
            if !valid_identifier(param) || !scope.insert(param.as_str()) {
                return Err(format!("invalid or duplicate MIR parameter '{param}'"));
            }
        }
        let mut count = 0usize;
        for (name, expr) in &function.locals {
            validate_expr(expr, &scope, &signatures, &mut count)?;
            if !valid_identifier(name) || !scope.insert(name.as_str()) {
                return Err(format!("invalid or duplicate MIR local '{name}'"));
            }
        }
        validate_expr(&function.return_value, &scope, &signatures, &mut count)?;
    }
    Ok(())
}
fn validate_expr(
    root: &Expr,
    scope: &std::collections::HashSet<&str>,
    signatures: &std::collections::HashMap<&str, usize>,
    count: &mut usize,
) -> Result<(), String> {
    let mut stack = vec![root];
    while let Some(expr) = stack.pop() {
        *count = count
            .checked_add(1)
            .ok_or("MIR expression count overflow")?;
        if *count > MAX_NODES_PER_FUNCTION {
            return Err("MIR expression exceeds bounded node budget".into());
        }
        match expr {
            Expr::Integer(_) => {}
            Expr::Name(name) => {
                if !scope.contains(name.as_str()) {
                    return Err(format!("undeclared MIR scalar '{name}'"));
                }
            }
            Expr::Negate(inner) => stack.push(inner),
            Expr::Add(a, b) | Expr::Subtract(a, b) | Expr::Multiply(a, b) => {
                stack.push(a);
                stack.push(b);
            }
            Expr::Call { name, args } => {
                if signatures.get(name.as_str()) != Some(&args.len()) {
                    return Err(format!("unresolved or wrong-arity MIR call '{name}'"));
                }
                stack.extend(args);
            }
        }
    }
    Ok(())
}

/// Explicit iterative postorder prevents exporter recursion exhaustion on
/// syntactically deep (but otherwise bounded) expression trees.
fn append(expr: &Expr, events: &mut Vec<Value>) {
    let mut stack = vec![(expr, false)];
    while let Some((node, emit)) = stack.pop() {
        match node {
            Expr::Integer(_) => events.push(json!({"kind": "compute", "units": 1})),
            Expr::Name(name) => events.push(json!({"kind": "read", "place": name})),
            Expr::Negate(a) => {
                if emit {
                    events.push(json!({"kind": "compute", "units": 1}));
                } else {
                    stack.push((node, true));
                    stack.push((a, false));
                }
            }
            Expr::Add(a, b) | Expr::Subtract(a, b) | Expr::Multiply(a, b) => {
                if emit {
                    events.push(json!({"kind": "compute", "units": 1}));
                } else {
                    stack.push((node, true));
                    stack.push((b, false));
                    stack.push((a, false));
                }
            }
            Expr::Call { name, args } => {
                if emit {
                    // MUST remain visible as a proof gap until independently
                    // checked compositional call summaries exist.
                    events.push(json!({"kind": "call", "target": name}));
                } else {
                    stack.push((node, true));
                    for arg in args.iter().rev() {
                        stack.push((arg, false));
                    }
                }
            }
        }
    }
}

/// The budget is provided by policy, not computed from CPU timing/WCET.
/// Runtime actor/Task/memory effects cannot occur in the admitted scalar slice.
pub fn export_cfg_v2(module: &Module, budget: Option<u64>) -> Result<Value, String> {
    validate(module)?;
    let functions = module
        .functions
        .iter()
        .map(|function| {
            let mut events = Vec::new();
            for name in &function.params {
                events.push(json!({"kind": "define", "place": name}));
            }
            for (name, expr) in &function.locals {
                append(expr, &mut events);
                events.push(json!({"kind": "define", "place": name}));
            }
            append(&function.return_value, &mut events);
            json!({
                "name": function.symbol,
                "entry": "entry",
                "max_uninterrupted_units": budget,
                "allow_error_propagation": false,
                "blocks": [
                    {"id": "entry", "events": events, "terminator": {"kind": "return"}}
                ]
            })
        })
        .collect::<Vec<_>>();
    let manifest = json!({
        "schema_version": 2,
        "effects_complete": true,
        "functions": functions
    });
    if serde_json::to_vec(&manifest)
        .map_err(|e| e.to_string())?
        .len()
        > MAX_OUTPUT_BYTES
    {
        return Err("CFG-v2 manifest exceeds analyzer input size budget".into());
    }
    Ok(manifest)
}
#[cfg(test)]
mod tests {
    use super::*;
    fn export(source: &str, budget: Option<u64>) -> Value {
        let ast = crate::frontend::parse(source).unwrap();
        let mir = crate::mir::lower(ast).unwrap();
        export_cfg_v2(&mir, budget).unwrap()
    }
    #[test]
    fn scalar_expression_has_one_cfg_return_block() {
        let v = export("pub fnc answer(): int { return 40 + 2; }", Some(10));
        assert_eq!(v["schema_version"], 2);
        assert_eq!(v["functions"][0]["entry"], "entry");
        assert_eq!(
            v["functions"][0]["blocks"][0]["events"]
                .as_array()
                .unwrap()
                .len(),
            3
        );
        assert_eq!(
            v["functions"][0]["blocks"][0]["terminator"]["kind"],
            "return"
        );
    }
    #[test]
    fn calls_remain_explicit_and_unverified() {
        let v = export(
            "pub fnc main(): int { return add(40,2); } fnc add(int a,int b): int { return a+b; }",
            Some(10),
        );
        assert!(v["functions"][0]["blocks"][0]["events"]
            .as_array()
            .unwrap()
            .iter()
            .any(|x| x["kind"] == "call" && x["target"] == "add"));
    }
    #[test]
    fn parameter_and_local_places_are_declared_before_read() {
        let v = export(
            "pub fnc use_number(int x): int { const y: int = x; return y; }",
            Some(10),
        );
        let e = v["functions"][0]["blocks"][0]["events"].as_array().unwrap();
        assert_eq!(e[0]["kind"], "define");
        assert_eq!(e[1]["kind"], "read");
        assert_eq!(e[2]["kind"], "define");
        assert_eq!(e[3]["kind"], "read");
    }
    #[test]
    fn forged_public_mir_cannot_launder_unknown_names() {
        let ast = crate::frontend::parse("pub fnc f(): int { return 2; }").unwrap();
        let mut module = crate::mir::lower(ast).unwrap();
        module.functions[0].return_value = Expr::Name("ghost".into());
        assert!(export_cfg_v2(&module, Some(10))
            .unwrap_err()
            .contains("undeclared"));
    }
    #[test]
    fn forged_public_mir_cannot_launder_undefined_calls() {
        let ast = crate::frontend::parse("pub fnc f(): int { return 2; }").unwrap();
        let mut module = crate::mir::lower(ast).unwrap();
        module.functions[0].return_value = Expr::Call {
            name: "external_without_summary".into(),
            args: vec![],
        };
        assert!(export_cfg_v2(&module, Some(10))
            .unwrap_err()
            .contains("wrong-arity"));
    }
    #[test]
    fn forged_duplicate_parameters_are_rejected() {
        let ast = crate::frontend::parse("pub fnc f(int a): int { return a; }").unwrap();
        let mut module = crate::mir::lower(ast).unwrap();
        module.functions[0].params.push("a".into());
        assert!(export_cfg_v2(&module, Some(10)).is_err());
    }
    #[test]
    fn forged_module_cannot_bypass_work_budget_by_truncation() {
        let ast = crate::frontend::parse("pub fnc f(): int { return 2; }").unwrap();
        let mut module = crate::mir::lower(ast).unwrap();
        for _ in 0..MAX_NODES_PER_FUNCTION {
            let old = std::mem::replace(&mut module.functions[0].return_value, Expr::Integer(0));
            module.functions[0].return_value = Expr::Negate(Box::new(old));
        }
        assert!(export_cfg_v2(&module, Some(10000)).is_err());
    }
    #[test]
    fn absent_budget_stays_absent_and_cannot_be_invented() {
        let v = export("pub fnc answer(): int { return 2; }", None);
        assert_eq!(v["functions"][0]["max_uninterrupted_units"], Value::Null);
    }
}
