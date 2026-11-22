//! Validated scalar MIR. This is intentionally an Oreslang IR, not Cranelift IR.
use crate::{
    frontend::{ExportDecl, Expr, Program},
    ref_layout::ManagedRefLayout,
};
use std::collections::{HashMap, HashSet};
use std::fmt;

#[derive(Clone, Debug)]
pub struct Module {
    pub functions: Vec<Function>,
    /// Qualified source export manifest. Kept even for package-only
    /// names that must never be marked as public native object symbols.
    pub exports: Vec<ExportDecl>,
    pub managed_refs: ManagedRefLayout,
}
#[derive(Clone, Debug)]
pub struct Function {
    pub symbol: String,
    pub exported: bool,
    pub params: Vec<String>,
    pub locals: Vec<(String, Expr)>,
    pub return_value: Expr,
}
#[derive(Debug, Clone)]
pub struct SemanticError {
    pub class: &'static str,
    pub message: String,
}
impl fmt::Display for SemanticError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.class, self.message)
    }
}
impl std::error::Error for SemanticError {}
fn failure(class: &'static str, message: impl Into<String>) -> SemanticError {
    SemanticError {
        class,
        message: message.into(),
    }
}
fn validate_expr(
    expr: &Expr,
    variables: &HashSet<String>,
    signatures: &HashMap<String, usize>,
) -> Result<(), SemanticError> {
    match expr {
        Expr::Integer(_) => Ok(()),
        Expr::Name(name) => {
            if variables.contains(name) {
                Ok(())
            } else {
                Err(failure(
                    "unknown_name",
                    format!("undeclared scalar '{name}'"),
                ))
            }
        }
        Expr::Call { name, args } => {
            let Some(expected) = signatures.get(name) else {
                return Err(failure(
                    "unknown_name",
                    format!("unknown function '{name}'"),
                ));
            };
            if args.len() != *expected {
                return Err(failure(
                    "call_arity",
                    format!(
                        "{name}: expected {expected} parameters, received {}",
                        args.len()
                    ),
                ));
            }
            for arg in args {
                validate_expr(arg, variables, signatures)?;
            }
            Ok(())
        }
        Expr::Add(a, b) | Expr::Subtract(a, b) | Expr::Multiply(a, b) => {
            validate_expr(a, variables, signatures)?;
            validate_expr(b, variables, signatures)
        }
        Expr::Negate(expr) => validate_expr(expr, variables, signatures),
    }
}
// Reserve canonical future collection, Option and intrinsic loan identities.
// This scalar-only compiler must not emit a native object containing a user
// function that impersonates a compiler-owned generic type constructor.
// No collection/borrow semantics are implemented by this guard.
const RESERVED_COLLECTION_NAMES: [&str; 13] = [
    "Option", "Result", "Array", "FixedArray", "Vector", "List",
    "FixedList", "Slice", "Tuple", "Map", "Set", "ReadLoan", "WriteLoan",
];

pub fn lower(program: Program) -> Result<Module, SemanticError> {
    lower_with_ref_layout(program, ManagedRefLayout::default())
}

pub fn lower_with_ref_layout(
    program: Program,
    managed_refs: ManagedRefLayout,
) -> Result<Module, SemanticError> {
    let mut signatures = HashMap::new();
    for function in &program.functions {
        if RESERVED_COLLECTION_NAMES.contains(&function.name.as_str()) {
            return Err(failure("reserved_collection_name",
                format!("'{}' is reserved for a canonical collection, Option or intrinsic loan type", function.name)));
        }
        if signatures
            .insert(function.name.clone(), function.params.len())
            .is_some()
        {
            return Err(failure(
                "duplicate_declaration",
                format!("duplicate function '{}'", function.name),
            ));
        }
    }
    let mut functions = Vec::with_capacity(program.functions.len());
    for function in program.functions {
        let mut vars = HashSet::new();
        for param in &function.params {
            if !vars.insert(param.clone()) {
                return Err(failure(
                    "duplicate_declaration",
                    format!("duplicate parameter '{param}'"),
                ));
            }
        }
        let mut locals = Vec::new();
        for local in function.locals {
            validate_expr(&local.value, &vars, &signatures)?;
            if !vars.insert(local.name.clone()) {
                return Err(failure(
                    "duplicate_declaration",
                    format!("duplicate local '{}'", local.name),
                ));
            }
            locals.push((local.name, local.value));
        }
        validate_expr(&function.expression, &vars, &signatures)?;
        functions.push(Function {
            symbol: function.name,
            exported: function.exported,
            params: function.params,
            locals,
            return_value: function.expression,
        });
    }
    Ok(Module {
        functions,
        exports: program.exports,
        managed_refs,
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    fn check(source: &str) -> Result<Module, SemanticError> {
        lower(crate::frontend::parse(source).unwrap())
    }
    #[test]
    fn managed_reference_layout_is_preserved_in_mir() {
        let program = crate::frontend::parse("pub fnc main(): int { return 42; }").unwrap();
        let module =
            lower_with_ref_layout(program, ManagedRefLayout::native()).expect("valid scalar MIR");
        assert_eq!(module.managed_refs, ManagedRefLayout::native());
    }

    #[test]
    fn duplicate_function_rejected() {
        assert_eq!(
            check("fnc f(): int { return 1; } fnc f(): int { return 2; }")
                .unwrap_err()
                .class,
            "duplicate_declaration"
        );
    }
    #[test]
    fn forward_call_admitted() {
        check("pub fnc main(): int { return times(7,6); } fnc times(int a,int b): int { return a*b; }").unwrap();
    }
    #[test]
    fn unknown_name_rejected() {
        assert_eq!(
            check("pub fnc main(): int { return neverDeclared; }")
                .unwrap_err()
                .class,
            "unknown_name"
        );
    }
    #[test]
    fn call_arity_rejected() {
        assert_eq!(
            check(
                "fnc add(int a,int b): int { return a+b; } pub fnc main(): int { return add(1); }"
            )
            .unwrap_err()
            .class,
            "call_arity"
        );
    }
    #[test]
    fn shadowed_local_rejected() {
        assert_eq!(
            check("fnc f(int a): int { const a: int = 3; return a; }")
                .unwrap_err()
                .class,
            "duplicate_declaration"
        );
    }
    #[test]
    fn local_binding_order_enforced() {
        assert_eq!(
            check("fnc f(): int { const a: int = b; return a; }")
                .unwrap_err()
                .class,
            "unknown_name"
        );
    }
}
