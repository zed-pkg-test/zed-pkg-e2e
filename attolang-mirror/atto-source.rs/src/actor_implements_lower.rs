//! Checked, same-source actor-local \`implements\` and immutable \`mtd\` lowering.
//!
//! The checked receiver body is attached as an ordinary method on the exact
//! actor declaration, then passed through the existing actor parser, borrow
//! checker, mailbox runtime and guest Wasm emitter. An ActorRef never acquires
//! synchronous external receiver dispatch. Structs and trait defaults remain
//! explicitly unsupported in this executable milestone.
use std::collections::{BTreeMap, BTreeSet};
use crate::diag::{Diagnostic, Pos};
use crate::lexer::{Tok, Token};

type R<T> = Result<T, Diagnostic>;
fn word(t: &[Token], i: usize, w: &str) -> bool {
    matches!(t.get(i).map(|x| &x.tok), Some(Tok::Ident(s)) if s == w)
}
fn error(t: &[Token], i: usize, code: &'static str, message: &str) -> Diagnostic {
    Diagnostic::new(t.get(i).map_or(Pos::default(), |x| x.pos), code, message)
}
fn pair(t: &[Token], start: usize, left: Tok, right: Tok) -> R<usize> {
    let mut n = 0usize;
    for (i, x) in t.iter().enumerate().skip(start) {
        if x.tok == left { n += 1; }
        if x.tok == right {
            if n == 0 { return Err(error(t, i, "E_SYNTAX", "unmatched delimiter")); }
            n -= 1;
            if n == 0 { return Ok(i); }
        }
        if n > 128 { return Err(error(t, i, "E_NESTING", "actor method nesting limit")); }
    }
    Err(error(t, start, "E_EOF", "unterminated receiver or body"))
}
fn nominal_body_end(t: &[Token], start: usize) -> R<usize> {
    let mut depth = 0usize;
    for i in start..t.len() {
        if t[i].tok == Tok::LBrace { depth += 1; }
        if t[i].tok == Tok::RBrace {
            if depth == 0 { return Err(error(t, i, "E_SYNTAX", "unexpected body close")); }
            depth -= 1;
        }
        if depth == 0 && word(t, i, "end") { return Ok(i); }
        if depth > 128 { return Err(error(t, i, "E_NESTING", "nominal declaration nesting limit")); }
    }
    Err(error(t, start, "E_EOF", "unterminated nominal declaration"))
}
fn owner_name(t: &[Token], i: usize) -> R<String> {
    match t.get(i).map(|x| &x.tok) {
        Some(Tok::Ident(n)) if n.len() <= 96 => Ok(n.clone()),
        _ => Err(error(t, i, "E_SYNTAX", "expected nominal actor name")),
    }
}
fn omit(removed: &mut [bool], t: &[Token], start: usize, end: usize) -> R<()> {
    if start >= end || end > t.len() || removed[start..end].iter().any(|x| *x) {
        return Err(error(t, start, "E_SYNTAX", "overlapping actor method/declaration ranges"));
    }
    removed[start..end].fill(true);
    Ok(())
}
/// Returns None if no actor implements header exists. Nonmatching source
/// must continue to the normal frontend exactly as before.
pub fn lower(t: &[Token]) -> R<Option<Vec<Token>>> {
    let mut targeted = false;
    let mut i = 0usize;
    while i < t.len() {
        if t[i].tok == Tok::LBrace {
            i = pair(t, i, Tok::LBrace, Tok::RBrace)? + 1;
            continue;
        }
        let j = i + usize::from(word(t, i, "pub"));
        if (word(t, j, "define") || word(t, j, "declare"))
            && word(t, j + 1, "actor")
            && word(t, j + 3, "implements") {
            targeted = true;
            break;
        }
        i += 1;
    }
    if !targeted { return Ok(None); }
    // No lowering or emitted bytes may precede the complete bounded
    // conformance/visibility/signature/duplicate check.
    crate::nominal_contract_admission::validate_actor_lowering(t)?;
    let mut removed = vec![false; t.len()];
    let mut insertions = BTreeMap::<usize, Vec<Token>>::new();
    let mut end_by_owner = BTreeMap::<String, usize>::new();
    let mut methods = BTreeMap::<String, Vec<Token>>::new();
    let mut definition_names = BTreeSet::new();
    let mut i = 0usize;
    while i < t.len() {
        if t[i].tok == Tok::LBrace {
            i = pair(t, i, Tok::LBrace, Tok::RBrace)? + 1;
            continue;
        }
        let beginning = i;
        let j = i + usize::from(word(t, i, "pub"));
        if word(t, j, "declare") || word(t, j, "define") {
            let defined = word(t, j, "define");
            let kind_at = j + 1 + usize::from(word(t, j + 1, "pub"));
            if word(t, kind_at, "actor") || word(t, kind_at, "interface")
                || word(t, kind_at, "trait") {
                let kind = owner_name(t, kind_at)?;
                let owner = owner_name(t, kind_at + 1)?;
                let mut header_end = kind_at + 2;
                if word(t, header_end, "extends") {
                    return Err(error(t, header_end, "E_UNSUPPORTED",
                        "actor-local contract lowering does not support inheritance"));
                }
                if word(t, header_end, "implements") {
                    header_end += 1;
                    while header_end < t.len() && !word(t, header_end, "as")
                        && t[header_end].tok != Tok::Semi {
                        header_end += 1;
                    }
                }
                if kind == "actor" && !defined {
                    if t.get(header_end).map(|x| &x.tok) != Some(&Tok::Semi) {
                        return Err(error(t, header_end, "E_SYNTAX",
                            "actor forward declaration must end with ';'"));
                    }
                    omit(&mut removed, t, beginning, header_end + 1)?;
                    i = header_end + 1; continue;
                }
                if !defined || !word(t, header_end, "as") {
                    return Err(error(t, header_end, "E_SYNTAX",
                        "nominal contract definition requires 'as'"));
                }
                let end = nominal_body_end(t, header_end + 1)?;
                if kind == "actor" {
                    // This first production lowering has no independently
                    // certified critical-read/write actor-state access path.
                    // Never let lexical rewriting bypass sealed actor fields:
                    // only genuinely stateless actor definitions are admitted.
                    let (mut braces, mut parens) = (0usize, 0usize);
                    for k in header_end + 1..end {
                        match t[k].tok {
                            Tok::LBrace => braces += 1,
                            Tok::RBrace => braces = braces.saturating_sub(1),
                            Tok::LParen => parens += 1,
                            Tok::RParen => parens = parens.saturating_sub(1),
                            _ => {}
                        }
                        if braces == 0 && parens == 0 {
                            let binder = ["state", "let", "val", "const", "final", "mut"]
                                .iter().any(|w| word(t, k, w));
                            let typed_field = matches!(&t[k].tok, Tok::Ident(n)
                                    if ["int", "bool", "float", "string"].contains(&n.as_str()))
                                && matches!(t.get(k + 1).map(|x| &x.tok), Some(Tok::Ident(_)));
                            let named_field = matches!(&t[k].tok, Tok::Ident(n)
                                    if !["receive", "fnc", "method", "pub", "private"].contains(&n.as_str()))
                                && t.get(k + 1).map(|x| &x.tok) == Some(&Tok::Colon);
                            if binder || typed_field || named_field {
                                return Err(error(t, k, "E_UNSUPPORTED",
                                    "actor implements mtd cannot access unverified sealed actor state"));
                            }
                        }
                    }
                    if !definition_names.insert(owner.clone()) {
                        return Err(error(t, beginning, "E_DUPLICATE", "actor defined more than once"));
                    }
                    insertions.entry(end).or_default();
                    end_by_owner.insert(owner, end);
                    // Erase only the checked implements list from an actor
                    // header; the entire actor body stays with the real
                    // runtime and ownership checker.
                    if header_end > kind_at + 2 {
                        omit(&mut removed, t, kind_at + 2, header_end)?;
                    }
                } else {
                    // Abstract interface and trait contracts have been
                    // validated; they are compile-time types, not actors.
                    omit(&mut removed, t, beginning, end + 1)?;
                }
                i = end + 1; continue;
            }
            if word(t, kind_at, "struct") || word(t, kind_at, "class") {
                return Err(error(t, beginning, "E_UNSUPPORTED",
                    "mixed class/struct actor-local contract lowering needs a linked nominal IR"));
            }
        }
        if word(t, j, "mtd") {
            let pub_method = j != beginning;
            if t.get(j + 1).map(|x| &x.tok) != Some(&Tok::LParen) {
                return Err(error(t, j, "E_SYNTAX", "receiver requires nominal parentheses"));
            }
            let close = pair(t, j + 1, Tok::LParen, Tok::RParen)?;
            let receiver = &t[j + 2..close];
            let owner = match receiver {
                [Token { tok: Tok::Ident(n), .. }, Token { tok: Tok::Ident(s), .. }] if s == "self" => n.clone(),
                [Token { tok: Tok::Ident(s), .. }, Token { tok: Tok::Colon, .. }, Token { tok: Tok::Ident(n), .. }]
                    if s == "self" => n.clone(),
                _ => return Err(error(t, j, "E_UNSUPPORTED",
                    "actor executable mtd requires immutable nominal self receiver")),
            };
            let method_start = close + 1;
            if !matches!(t.get(method_start).map(|x| &x.tok), Some(Tok::Ident(_)))
                || t.get(method_start + 1).map(|x| &x.tok) != Some(&Tok::LParen) {
                return Err(error(t, method_start, "E_UNSUPPORTED",
                    "actor mtd requires a normal named method signature"));
            }
            let params_end = pair(t, method_start + 1, Tok::LParen, Tok::RParen)?;
            let body_start = (params_end + 1..t.len())
                .find(|&k| t[k].tok == Tok::LBrace || t[k].tok == Tok::Semi)
                .ok_or_else(|| error(t, params_end, "E_EOF", "actor method body missing"))?;
            if t[body_start].tok != Tok::LBrace {
                return Err(error(t, body_start, "E_IMPL_MISSING",
                    "actor mtd requires a concrete method body"));
            }
            let end = pair(t, body_start, Tok::LBrace, Tok::RBrace)? + 1;
            let tokens = methods.entry(owner).or_default();
            if pub_method { tokens.push(t[beginning].clone()); }
            tokens.extend_from_slice(&t[method_start..end]);
            omit(&mut removed, t, beginning, end)?;
            i = end; continue;
        }
        i += 1;
    }
    for (owner, tokens) in methods {
        let end = end_by_owner.get(&owner).ok_or_else(|| error(t, 0, "E_IMPL_UNRESOLVED",
            "actor out-of-line method requires a defined local owner"))?;
        insertions.get_mut(end).expect("registered concrete actor end").extend(tokens);
    }
    let mut result = Vec::with_capacity(t.len());
    for (i, x) in t.iter().enumerate() {
        if let Some(add) = insertions.get(&i) { result.extend(add.iter().cloned()); }
        if !removed[i] { result.push(x.clone()); }
    }
    if result.len() > crate::lexer::MAX_TOKENS {
        return Err(error(t, 0, "E_SOURCE_LIMIT", "expanded actor method tokens exceed budget"));
    }
    Ok(Some(result))
}
