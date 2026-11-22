//! Backend-neutral build profile enforcement for the Rust/Cranelift compiler.
//!
//! Runtime reflection does not exist in the admitted scalar subset. Release or
//! stronger builds compute a closed reachable function set before codegen.
use crate::{frontend::Expr, mir::Module};
use std::collections::{HashMap, HashSet};

const HARD_RT_MAX_FUNCTIONS: usize = 256;
const HARD_RT_MAX_CALL_DEPTH: usize = 64;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BuildProfile {
    Dev,
    Release,
    StrictAot,
    HardRt,
}

impl BuildProfile {
    pub fn parse_flag(flag: &str) -> Option<Self> {
        match flag {
            "--release" => Some(Self::Release),
            "--strict-aot" => Some(Self::StrictAot),
            "--hard-rt" => Some(Self::HardRt),
            _ => None,
        }
    }

    pub fn requires_tree_shaking(self) -> bool {
        !matches!(self, Self::Dev)
    }
}

fn collect_calls(expr: &Expr, out: &mut Vec<String>) {
    match expr {
        Expr::Integer(_) | Expr::Name(_) => {}
        Expr::Negate(value) => collect_calls(value, out),
        Expr::Add(a, b) | Expr::Subtract(a, b) | Expr::Multiply(a, b) => {
            collect_calls(a, out);
            collect_calls(b, out);
        }
        Expr::Call { name, args } => {
            out.push(name.clone());
            for arg in args {
                collect_calls(arg, out);
            }
        }
    }
}

fn call_graph(module: &Module) -> HashMap<String, Vec<String>> {
    module
        .functions
        .iter()
        .map(|function| {
            let mut calls = Vec::new();
            for (_, expr) in &function.locals {
                collect_calls(expr, &mut calls);
            }
            collect_calls(&function.return_value, &mut calls);
            (function.symbol.clone(), calls)
        })
        .collect()
}

/// Remove private functions unreachable from the executable/public roots.
///
/// Public exports are roots because object files can also be consumed as
/// libraries. A function named main is always a root when present.
pub fn tree_shake(mut module: Module) -> Result<Module, String> {
    let graph = call_graph(&module);
    let mut pending: Vec<String> = module
        .functions
        .iter()
        .filter(|function| function.exported || function.symbol == "main")
        .map(|function| function.symbol.clone())
        .collect();
    // A package-only export is not a public native symbol, but its body
    // must survive release/strict-AOT tree shaking for package-level use.
    // Keep every validated explicit export as an additional reachability root.
    pending.extend(module.exports.iter().map(|export| export.name.clone()));
    if pending.is_empty() {
        return Err("release tree shaking requires main or at least one exported root".into());
    }

    let mut live = HashSet::new();
    while let Some(symbol) = pending.pop() {
        if !live.insert(symbol.clone()) {
            continue;
        }
        let edges = graph
            .get(&symbol)
            .ok_or_else(|| format!("tree-shake root references missing function '{symbol}'"))?;
        for callee in edges {
            if !live.contains(callee) {
                pending.push(callee.clone());
            }
        }
    }
    module
        .functions
        .retain(|function| live.contains(&function.symbol));
    Ok(module)
}

/// Pre-admission for the current hard-RT subset.
///
/// The scalar language has no heap, GC, dynamic dispatch, blocking operation,
/// loop or runtime reflection. We additionally reject recursion and cap static
/// call depth. Target-specific WCET evidence is deliberately not fabricated:
/// until that evidence generator exists, --hard-rt fails closed after these
/// source/MIR checks.
pub fn validate_hard_rt_preconditions(module: &Module) -> Result<(), String> {
    if module.functions.len() > HARD_RT_MAX_FUNCTIONS {
        return Err(format!(
            "hard-rt function budget exceeded ({HARD_RT_MAX_FUNCTIONS})"
        ));
    }
    let graph = call_graph(module);
    let mut visiting = HashSet::new();
    let mut memo = HashMap::<String, usize>::new();

    fn depth(
        name: &str,
        graph: &HashMap<String, Vec<String>>,
        visiting: &mut HashSet<String>,
        memo: &mut HashMap<String, usize>,
    ) -> Result<usize, String> {
        if let Some(value) = memo.get(name) {
            return Ok(*value);
        }
        if !visiting.insert(name.to_owned()) {
            return Err(format!("hard-rt rejects recursive call graph at '{name}'"));
        }
        let calls = graph
            .get(name)
            .ok_or_else(|| format!("hard-rt unresolved call target '{name}'"))?;
        let mut result = 1usize;
        for callee in calls {
            let child = depth(callee, graph, visiting, memo)?;
            result = result.max(child.saturating_add(1));
            if result > HARD_RT_MAX_CALL_DEPTH {
                return Err(format!(
                    "hard-rt call depth exceeds {HARD_RT_MAX_CALL_DEPTH}"
                ));
            }
        }
        visiting.remove(name);
        memo.insert(name.to_owned(), result);
        Ok(result)
    }

    for name in graph.keys() {
        depth(name, &graph, &mut visiting, &mut memo)?;
    }
    Ok(())
}

pub fn apply(module: Module, profile: BuildProfile) -> Result<Module, String> {
    let module = if profile.requires_tree_shaking() {
        tree_shake(module)?
    } else {
        module
    };
    if profile == BuildProfile::HardRt {
        validate_hard_rt_preconditions(&module)?;
        return Err(
            "hard-rt preconditions passed, but target WCET/deadline evidence is not implemented; refusing to claim hard-rt".into()
        );
    }
    Ok(module)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lowered(source: &str) -> Module {
        crate::mir::lower(crate::frontend::parse(source).unwrap()).unwrap()
    }

    #[test]
    fn release_removes_unreachable_private_function() {
        let module = lowered(
            "pub fnc main(): int { return used(); } fnc used(): int { return 7; } fnc dead(): int { return 9; }"
        );
        let shaken = tree_shake(module).unwrap();
        let names: Vec<_> = shaken.functions.iter().map(|f| f.symbol.as_str()).collect();
        assert_eq!(names, vec!["main", "used"]);
    }

    #[test]
    fn hard_rt_preflight_rejects_recursion() {
        let module = lowered(
            "pub fnc main(): int { return recurse(); } fnc recurse(): int { return recurse(); }",
        );
        let error = validate_hard_rt_preconditions(&module).unwrap_err();
        assert!(error.contains("recursive call graph"), "{error}");
    }

    #[test]
    fn hard_rt_fails_closed_without_wcet_evidence() {
        let module = lowered("pub fnc main(): int { return 42; }");
        let error = apply(module, BuildProfile::HardRt).unwrap_err();
        assert!(error.contains("WCET/deadline evidence"), "{error}");
    }
}
