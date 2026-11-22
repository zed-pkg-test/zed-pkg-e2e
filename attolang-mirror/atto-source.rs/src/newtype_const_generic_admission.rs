//! Source-exact, fail-closed admission for AttoLang nominal scalar newtypes and
//! compile-time integer dimensions. This pass runs BEFORE any Wasm producer.
//!
//! It validates a deliberately bounded integer proof slice and rejects valid
//! declarations with E_UNSUPPORTED until typed IR, checked guest constructors,
//! and target layouts have been proven. No token erasure or runtime fallback.

use crate::diag::{Diagnostic, Pos};
use crate::lexer::{Tok, Token};
use std::collections::{BTreeMap, BTreeSet};

type R<T> = Result<T, Diagnostic>;
const MAX_DECLS: usize = 64;
const MAX_EXPR_TOKENS: usize = 64;
const MAX_INLINE_ELEMENTS: u64 = 1_048_576;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Interval {
    pub low: i64,
    pub high: i64,
}

impl Interval {
    pub fn contains(self, value: i64) -> bool {
        value >= self.low && value <= self.high
    }
}

#[derive(Clone, Debug, Default)]
pub struct Admission {
    pub nominal: BTreeMap<String, Interval>,
    pub known_lengths: Vec<u64>,
    pub requires_backend: bool,
}

fn at(t: &[Token], i: usize) -> Option<&Tok> {
    t.get(i).map(|item| &item.tok)
}

fn word(t: &[Token], i: usize, s: &str) -> bool {
    matches!(at(t, i), Some(Tok::Ident(w)) if w == s)
}

fn ident(t: &[Token], i: usize) -> Option<&str> {
    match at(t, i) {
        Some(Tok::Ident(w)) => Some(w),
        _ => None,
    }
}

fn error(t: &[Token], i: usize, code: &'static str, detail: &str) -> Diagnostic {
    Diagnostic::new(t.get(i).map_or(Pos::default(), |x| x.pos), code, detail)
}

fn signed(t: &[Token], at_index: &mut usize) -> R<i64> {
    let index = *at_index;
    let neg = at(t, index) == Some(&Tok::Minus);
    if neg {
        *at_index += 1;
    }
    let result = match at(t, *at_index) {
        Some(Tok::Int(Some(x))) if *x <= i64::MAX as u64 =>
            if neg { -(*x as i64) } else { *x as i64 },
        Some(Tok::Int(None)) if neg => i64::MIN,
        _ => return Err(error(t, index, "E_RANGE_INVALID",
            "refinement bound must be a representable, exact signed i64 literal")),
    };
    *at_index += 1;
    Ok(result)
}

fn parse_newtype(t: &[Token], i: usize, result: &mut Admission) -> R<usize> {
    let mut p = i + 1;
    if word(t, p, "pub") { p += 1; }
    if !word(t, p, "newtype") {
        return Ok(i + 1);
    }
    p += 1;
    let name = ident(t, p).ok_or_else(||
        error(t, p, "E_SYNTAX", "newtype requires an identifier"))?.to_string();
    if name.len() > 128 || crate::parser::is_reserved(&name)
        || matches!(name.as_str(), "int" | "i64" | "u64" | "usize")
    {
        return Err(error(t, p, "E_SYNTAX", "newtype name is invalid or reserved"));
    }
    if result.nominal.len() >= MAX_DECLS {
        return Err(error(t, p, "E_SOURCE_LIMIT", "newtype declaration budget exceeded"));
    }
    if result.nominal.contains_key(&name) {
        return Err(error(t, p, "E_DUPLICATE", "duplicate nominal newtype declaration"));
    }
    p += 1;
    if at(t, p) != Some(&Tok::LParen) {
        return Err(error(t, p, "E_SYNTAX", "newtype requires a base type in parentheses"));
    }
    p += 1;
    if !word(t, p, "i64") && !word(t, p, "int") {
        return Err(error(t, p, "E_UNSUPPORTED_TYPE",
            "integer proof core currently supports only the signed i64/int base"));
    }
    p += 1;
    if at(t, p) != Some(&Tok::RParen) {
        return Err(error(t, p, "E_SYNTAX", "expected ')' after newtype base"));
    }
    p += 1;
    let mut range = Interval { low: i64::MIN, high: i64::MAX };
    if word(t, p, "where") {
        p += 1;
        let mut comparisons = 0usize;
        loop {
            if !word(t, p, "value") {
                return Err(error(t, p, "E_RANGE_UNPROVEN",
                    "only exact value gte/lte integer comparisons are proved"));
            }
            p += 1;
            let is_low = word(t, p, "gte") || at(t, p) == Some(&Tok::Ge);
            let is_high = word(t, p, "lte") || at(t, p) == Some(&Tok::Le);
            if !is_low && !is_high {
                return Err(error(t, p, "E_RANGE_UNPROVEN",
                    "unsupported refinement operator; use gte/lte or >=/<= only"));
            }
            p += 1;
            let bound = signed(t, &mut p)?;
            if is_low { range.low = range.low.max(bound); }
            if is_high { range.high = range.high.min(bound); }
            if range.low > range.high {
                return Err(error(t, p.saturating_sub(1), "E_RANGE_INVALID",
                    "empty or contradictory integer refinement domain"));
            }
            comparisons += 1;
            if comparisons > 16 {
                return Err(error(t, p, "E_SOURCE_LIMIT", "refinement comparison budget exceeded"));
            }
            if word(t, p, "and") {
                p += 1;
                continue;
            }
            break;
        }
        if at(t, p) != Some(&Tok::Semi) || !word(t, p + 1, "end") {
            return Err(error(t, p, "E_RANGE_UNPROVEN",
                "only conjunctions of closed exact bounds are admitted"));
        }
        p += 2;
        if at(t, p) == Some(&Tok::Semi) { p += 1; }
    } else if at(t, p) == Some(&Tok::Semi) {
        p += 1;
    } else {
        return Err(error(t, p, "E_SYNTAX", "expected ';' or supported 'where' clause"));
    }
    result.nominal.insert(name, range);
    result.requires_backend = true;
    Ok(p)
}

fn exact_const(t: &[Token], index: &mut usize, end: usize, depth: usize) -> R<u64> {
    if depth > 16 || end.saturating_sub(*index) > MAX_EXPR_TOKENS {
        return Err(error(t, *index, "E_CONST_GENERIC_ARG", "const expression budget exceeded"));
    }
    let mut lhs = exact_product(t, index, end, depth + 1)?;
    while *index < end {
        let add = match at(t, *index) {
            Some(Tok::Plus) => true,
            Some(Tok::Minus) => false,
            _ => break,
        };
        *index += 1;
        let rhs = exact_product(t, index, end, depth + 1)?;
        lhs = if add { lhs.checked_add(rhs) } else { lhs.checked_sub(rhs) }
            .ok_or_else(|| error(t, *index - 1, "E_CONST_OVERFLOW",
                "compile-time dimension exceeds target usize arithmetic"))?;
    }
    Ok(lhs)
}

fn exact_product(t: &[Token], index: &mut usize, end: usize, depth: usize) -> R<u64> {
    let mut lhs = exact_factor(t, index, end, depth)?;
    while *index < end && at(t, *index) == Some(&Tok::Star) {
        *index += 1;
        let rhs = exact_factor(t, index, end, depth)?;
        lhs = lhs.checked_mul(rhs)
            .ok_or_else(|| error(t, *index - 1, "E_CONST_OVERFLOW",
                "compile-time dimension multiplication overflows usize"))?;
    }
    Ok(lhs)
}

fn exact_factor(t: &[Token], index: &mut usize, end: usize, depth: usize) -> R<u64> {
    if *index >= end || depth > 16 {
        return Err(error(t, *index, "E_CONST_GENERIC_ARG", "incomplete constant dimension"));
    }
    let current = *index;
    match at(t, current) {
        Some(Tok::Int(Some(v))) => {
            *index += 1;
            Ok(*v)
        }
        Some(Tok::Int(None)) => {
            // The lexer reserves None for the exact 2^63 literal, which is
            // invalid as signed i64 but representable as 64-bit usize.
            *index += 1;
            Ok(1_u64 << 63)
        }
        Some(Tok::LParen) => {
            *index += 1;
            let n = exact_const(t, index, end, depth + 1)?;
            if at(t, *index) != Some(&Tok::RParen) {
                return Err(error(t, *index, "E_CONST_GENERIC_ARG",
                    "expected ')' in compile-time dimension"));
            }
            *index += 1;
            Ok(n)
        }
        _ => Err(error(t, current, "E_CONST_GENERIC_ARG",
            "dimension is not a statically proven nonnegative integer")),
    }
}

fn array_dimensions(t: &[Token], i: usize, formals: &BTreeSet<String>,
    admission: &mut Admission) -> R<usize>
{
    // Only the simple Array<Scalar, expression> grammar is given diagnostics.
    // Nested/unknown type expressions remain unlowered by the production parser.
    if at(t, i + 1) != Some(&Tok::Lt) { return Ok(i + 1); }
    let mut p = i + 2;
    let mut nest = 1usize;
    let mut comma = None;
    while p < t.len() && nest > 0 && p - i < 160 {
        match at(t, p) {
            Some(Tok::Lt) => nest += 1,
            Some(Tok::Gt) => nest -= 1,
            Some(Tok::Comma) if nest == 1 => {
                if let Some(first) = comma {
                    // Named layout arguments belong to the existing
                    // collection-layout checker; do not interpret their
                    // comma-separated options as const dimensions.
                    let has_named_first = matches!(at(t, i + 2), Some(Tok::Ident(_))) &&
                        at(t, i + 3) == Some(&Tok::Assign);
                    let has_named_after_first_comma =
                        matches!(at(t, first + 1), Some(Tok::Ident(_))) &&
                        at(t, first + 2) == Some(&Tok::Assign);
                    if !has_named_first && !has_named_after_first_comma {
                        return Err(error(t, p, "E_CONST_GENERIC_ARG",
                            "positional fixed Array accepts only one dimension"));
                    }
                } else { comma = Some(p); }
            }
            Some(Tok::Eof) => break,
            _ => {}
        }
        p += 1;
    }
    if nest != 0 {
        return Err(error(t, i, "E_CONST_GENERIC_ARG", "unclosed Array dimensions"));
    }
    let Some(divider) = comma else { return Ok(p); };
    // Legacy Array<Particle, layout=soa> and even malformed
    // Array<layout=soa, Particle> belong to the existing collection layout
    // checker, which issues E_LAYOUT_VALUE or E_GENERIC_ORDER as appropriate.
    // Only positional Array<T, N> enters the const usize evaluator.
    let named_first = matches!(at(t, i + 2), Some(Tok::Ident(_))) &&
        at(t, i + 3) == Some(&Tok::Assign);
    let named_second = matches!(at(t, divider + 1), Some(Tok::Ident(_))) &&
        at(t, divider + 2) == Some(&Tok::Assign);
    if named_first || named_second {
        return Ok(p);
    }
    let end = p - 1;
    if divider + 1 == end {
        return Err(error(t, divider, "E_CONST_GENERIC_ARG", "empty const Array dimension"));
    }
    if t[divider + 1..end].iter().any(|tok| matches!(&tok.tok,
        Tok::Ident(s) if formals.contains(s))) {
        if t[divider + 1..end].iter().any(|tok| matches!(&tok.tok,
            Tok::Ident(s) if !formals.contains(s))) {
            return Err(error(t, divider + 1, "E_CONST_GENERIC_ARG",
                "unknown compile-time dimension binder"));
        }
        admission.requires_backend = true;
        return Ok(p);
    }
    let mut cursor = divider + 1;
    let value = exact_const(t, &mut cursor, end, 0)?;
    if cursor != end {
        return Err(error(t, cursor, "E_CONST_GENERIC_ARG",
            "unsupported trailing const dimension expression"));
    }
    if value > MAX_INLINE_ELEMENTS {
        return Err(error(t, divider, "E_CONST_LAYOUT_BUDGET",
            "fixed Array element count exceeds bounded browser layout budget"));
    }
    admission.known_lengths.push(value);
    admission.requires_backend = true;
    Ok(p)
}

pub fn inspect(t: &[Token]) -> R<Admission> {
    let mut result = Admission::default();
    let mut formals = BTreeSet::new();
    let mut local_formals = BTreeSet::new();
    let mut angle_depth = 0usize;
    // Collect declared const value binders. This does not prove their use or
    // general generic instantiation; positive forms still cannot emit Wasm.
    for i in 0..t.len() {
        match at(t, i) {
            Some(Tok::Lt) => angle_depth += 1,
            Some(Tok::Gt) => {
                angle_depth = angle_depth.saturating_sub(1);
                if angle_depth == 0 { local_formals.clear(); }
            }
            _ => {}
        }
        if word(t, i, "const") && word(t, i + 1, "usize") {
            let name = ident(t, i + 2).ok_or_else(||
                error(t, i + 2, "E_CONST_GENERIC_ARG", "missing const generic parameter"))?;
            if angle_depth > 0 && !local_formals.insert(name.to_owned()) {
                return Err(error(t, i + 2, "E_DUPLICATE", "duplicate const parameter"));
            }
            formals.insert(name.to_owned());
            result.requires_backend = true;
        }
    }

    let mut braces = 0usize;
    let mut i = 0;
    while i < t.len() {
        if braces == 0 && word(t, i, "define")
            && (word(t, i + 1, "newtype")
                || word(t, i + 1, "pub") && word(t, i + 2, "newtype"))
        {
            i = parse_newtype(t, i, &mut result)?;
            continue;
        }
        if word(t, i, "Array") && at(t, i + 1) == Some(&Tok::Lt) {
            i = array_dimensions(t, i, &formals, &mut result)?;
            continue;
        }
        match at(t, i) {
            Some(Tok::LBrace) => braces += 1,
            Some(Tok::RBrace) => braces = braces.saturating_sub(1),
            _ => {}
        }
        i += 1;
    }

    // All nominal checks below operate on tokenized input, never text or
    // substring patterns. No positive program reaches codegen in this slice.
    let mut bindings: BTreeMap<String, String> = BTreeMap::new();
    let mut function_params: BTreeMap<String, String> = BTreeMap::new();
    for i in 0..t.len() {
        if word(t, i, "fnc") && at(t, i + 2) == Some(&Tok::LParen) {
            if let (Some(func), Some(param_type)) = (ident(t, i + 1), ident(t, i + 3)) {
                if result.nominal.contains_key(param_type) && ident(t, i + 4).is_some() {
                    function_params.insert(func.to_owned(), param_type.to_owned());
                }
            }
        }
        if (word(t, i, "val") || word(t, i, "let") || word(t, i, "const"))
            && ident(t, i + 1).is_some_and(|name| result.nominal.contains_key(name))
        {
            let dest = ident(t, i + 1).unwrap();
            let variable = ident(t, i + 2).ok_or_else(||
                error(t, i + 2, "E_NEWTYPE_CONSTRUCTION", "missing nominal binding name"))?;
            if at(t, i + 3) != Some(&Tok::Assign) {
                return Err(error(t, i + 3, "E_NEWTYPE_CONSTRUCTION",
                    "newtype binding requires an explicit checked constructor"));
            }
            let rhs = i + 4;
            if let Some(rhs_type) = ident(t, rhs) {
                if result.nominal.contains_key(rhs_type)
                    && rhs_type != dest
                {
                    return Err(error(t, rhs, "E_NEWTYPE_MISMATCH",
                        "distinct opaque newtypes are never assignment-compatible"));
                }
                if let Some(existing) = bindings.get(rhs_type) {
                    if existing != dest {
                        return Err(error(t, rhs, "E_NEWTYPE_MISMATCH",
                            "nominal binding uses an incompatible value"));
                    }
                }
            }
            if at(t, rhs) != Some(&Tok::Ident(dest.to_owned()))
                && ident(t, rhs).and_then(|s| bindings.get(s))
                    .map(String::as_str) != Some(dest)
            {
                return Err(error(t, rhs, "E_NEWTYPE_CONSTRUCTION",
                    "raw base values cannot construct nominal types"));
            }
            if bindings.insert(variable.to_owned(), dest.to_owned()).is_some() {
                return Err(error(t, i + 2, "E_UNSUPPORTED",
                    "shadowed nominal binding needs verified scope-aware typing"));
            }
        }
    }

    for i in 0..t.len() {
        if let (Some(fn_name), Some(Tok::LParen)) = (ident(t, i), at(t, i + 1)) {
            if word(t, i.saturating_sub(1), "fnc") { continue; }
            if let Some(expected) = function_params.get(fn_name) {
                if let Some(found) = ident(t, i + 2).and_then(|arg| bindings.get(arg)) {
                    if found != expected {
                        return Err(error(t, i + 2, "E_NEWTYPE_MISMATCH",
                            "call uses an incompatible nominal newtype"));
                    }
                }
            }
        }
        if let (Some(name), Some(Tok::Dot)) = (ident(t, i), at(t, i + 1)) {
            if let Some(bounds) = result.nominal.get(name) {
                if word(t, i + 2, "from_const") && at(t, i + 3) == Some(&Tok::LParen) {
                    let mut value_start = i + 4;
                    if !matches!(at(t, value_start), Some(Tok::Int(_)) | Some(Tok::Minus)) {
                        return Err(error(t, value_start, "E_RANGE_UNPROVEN",
                            "from_const requires an exact integer literal"));
                    }
                    let value = signed(t, &mut value_start)?;
                    if at(t, value_start) != Some(&Tok::RParen) {
                        return Err(error(t, value_start, "E_RANGE_UNPROVEN",
                            "from_const requires a statically proven integer literal"));
                    }
                    if !bounds.contains(value) {
                        return Err(error(t, i + 4, "E_RANGE_INVALID",
                            "nominal constant violates its declared interval"));
                    }
                }
                if word(t, i + 2, "try_from") {
                    result.requires_backend = true;
                }
            }
        }
    }
    Ok(result)
}

/// Called by the real compiler before ANY WebAssembly producer. A successfully
/// parsed declaration stays fail-closed until executable, typed guest lowering.
pub fn preflight(t: &[Token]) -> R<()> {
    let admitted = inspect(t)?;
    if admitted.requires_backend {
        return Err(Diagnostic::new(t.first().map_or(Pos::default(), |x| x.pos),
            "E_UNSUPPORTED",
            "newtype and const-generic syntax validated, but this browser backend lacks verified nominal guest construction and fixed-layout specialization"));
    }
    Ok(())
}


#[cfg(test)]
mod tests {
    use super::*;
    use crate::lexer::lex;

    fn analyze(source: &str) -> R<Admission> {
        inspect(&lex(source).expect("lexed AttoLang source"))
    }
    fn rejects(source: &str, expected: &str) {
        let error = analyze(source).expect_err("must fail closed");
        assert_eq!(error.code, expected, "{source}: {error}");
    }

    #[test]
    fn exact_i64_bounds_and_minimum_value() {
        let program = analyze(
            "define newtype Port(i64) where value >= 1 and value <= 65535; end"
        ).unwrap();
        assert_eq!(program.nominal["Port"], Interval {low: 1, high: 65535});
        assert!(program.requires_backend);
        let min = analyze("define newtype Min(i64) where value gte -9223372036854775808; end").unwrap();
        assert!(min.nominal["Min"].contains(i64::MIN));
    }

    #[test]
    fn nominal_ranges_reject_unsafe_values_and_unknown_predicates() {
        rejects("define newtype Port(i64) where value gte 1 and value lte 65535; end fnc main(): int { val Port p = Port.from_const(0); return 42; }",
            "E_RANGE_INVALID");
        rejects("define newtype P(i64) where value gte 9 and value lte 8; end",
            "E_RANGE_INVALID");
        rejects("define newtype P(i64) where is_prime(value); end", "E_RANGE_UNPROVEN");
    }

    #[test]
    fn nominal_identity_cannot_be_replaced_with_same_base_storage() {
        rejects("define newtype UserId(i64); define newtype OrderId(i64); fnc accept(UserId id) -> int { return 42; } fnc main(): int { val OrderId order = OrderId.from_const(7); return accept(order); }",
            "E_NEWTYPE_MISMATCH");
        rejects("define newtype UserId(i64); fnc main(): int { val UserId id = 7; return 42; }",
            "E_NEWTYPE_CONSTRUCTION");
    }

    #[test]
    fn bounded_dimensions_and_arithmetic() {
        let a = analyze("fnc main(): int { val Array<int, 4 * 4> a = 0; return 42; }").unwrap();
        assert_eq!(a.known_lengths, vec![16]);
        rejects("fnc main(): int { val Array<int, runtime_n> a = 0; return 42; }",
            "E_CONST_GENERIC_ARG");
        rejects("fnc main(): int { val Array<int, 9223372036854775807 * 3> a = 0; return 42; }",
            "E_CONST_OVERFLOW");
        rejects("fnc main(): int { val Array<int, 1025 * 1024> a = 0; return 42; }",
            "E_CONST_LAYOUT_BUDGET");
    }

    #[test]
    fn integer_unsigned_2_pow_63_is_exact_and_not_silently_sign_wrapped() {
        rejects("fnc main(): int { val Array<int, 9223372036854775808 * 2> a = 0; return 42; }",
            "E_CONST_OVERFLOW");
        let a = analyze("fnc main(): int { val Array<int, 9223372036854775808 - 9223372036854775807> a = 0; return 42; }").unwrap();
        assert_eq!(a.known_lengths, vec![1]);
    }

    #[test]
    fn distinct_generic_headers_can_reuse_parameter_names() {
        let p = analyze("define struct Matrix<T, const usize N> as end define struct Vector<T, const usize N> as end").unwrap();
        assert!(p.requires_backend);
        rejects("define struct Pair<T, const usize N, const usize N> as end", "E_DUPLICATE");
    }

    #[test]
    fn named_array_layout_preserves_existing_collection_layout_admission() {
        for src in [
            "fnc main(): int { val Array<Particle, layout=soa> a = 0; return 42; }",
            "fnc main(): int { val Array<Particle, layout=aosoa(4)> a = 0; return 42; }",
            "fnc main(): int { val Array<Particle, layout=aos, capacity=8> a = 0; return 42; }",
            "fnc main(): int { val Array<layout=soa, Particle> a = 0; return 42; }",
        ] {
            let proof = analyze(src).expect("named physical layouts are not const dimensions");
            assert!(proof.known_lengths.is_empty(), "{src}");
        }
        rejects("fnc main(): int { val Array<int, 4, 5> a = 0; return 42; }",
            "E_CONST_GENERIC_ARG");
    }

    #[test]
    fn ordinary_scalar_source_remains_unaffected() {
        let p = analyze("fnc main(): int { return 42; }").unwrap();
        assert!(!p.requires_backend);
        assert!(p.nominal.is_empty());
        assert!(p.known_lengths.is_empty());
        preflight(&lex("fnc main(): int { return 42; }").unwrap()).unwrap();
        assert_eq!(preflight(&lex("define newtype Port(i64);").unwrap()).unwrap_err().code,
            "E_UNSUPPORTED");
    }
}
