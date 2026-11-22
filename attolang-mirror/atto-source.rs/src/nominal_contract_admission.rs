//! Early, bounded admission for actor/struct \`implements\` contracts.
//!
//! Runs before target-only syntax and special guest lowering. Unlike class
//! nominal_lower, this pass does NOT lower actor or struct receiver bodies.
//! It diagnoses provably broken contracts and refuses currently unverified
//! actor/struct ABI on otherwise valid contracts. Do not interpret successful
//! preflight as a compiled value or remotely callable actor.
use std::collections::BTreeMap;
use crate::diag::{Diagnostic, Pos};
use crate::lexer::{Tok, Token};

type R<T> = Result<T, Diagnostic>;
const MAX_TYPES: usize = 128;
const MAX_MEMBERS: usize = 256;
const MAX_CONTRACTS: usize = 16;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Kind { Actor, Struct, Class, Trait, Interface }

#[derive(Clone, Debug)]
struct Method {
    sig: Vec<Tok>,
    public: bool,
    concrete: bool,
    pos: Pos,
}

#[derive(Clone)]
struct Owner {
    kind: Kind,
    pos: Pos,
    contracts: Vec<String>,
    methods: BTreeMap<String, Method>,
}

fn is(t: &[Token], i: usize, word: &str) -> bool {
    matches!(t.get(i).map(|x| &x.tok), Some(Tok::Ident(s)) if s == word)
}
fn err(pos: Pos, code: &'static str, what: impl Into<String>) -> Diagnostic {
    Diagnostic::new(pos, code, what)
}
fn identifier(t: &[Token], i: usize) -> R<String> {
    match t.get(i) {
        Some(Token { tok: Tok::Ident(s), .. }) if s.len() <= 96
            && !matches!(s.as_str(), "as" | "end" | "implements" | "extends" | "define") => Ok(s.clone()),
        Some(t) => Err(err(t.pos, "E_SYNTAX", "expected nominal identifier")),
        None => Err(err(Pos::default(), "E_EOF", "missing nominal identifier")),
    }
}
fn match_pair(t: &[Token], start: usize, left: Tok, right: Tok) -> R<usize> {
    let mut depth = 0usize;
    for (i, x) in t.iter().enumerate().skip(start) {
        if x.tok == left { depth += 1; }
        if x.tok == right {
            if depth == 0 { return Err(err(x.pos, "E_SYNTAX", "unmatched close delimiter")); }
            depth -= 1;
            if depth == 0 { return Ok(i); }
        }
        if depth > 128 { return Err(err(x.pos, "E_NESTING", "receiver nesting exceeds 128")); }
    }
    Err(err(t.get(start).map_or(Pos::default(), |x| x.pos), "E_EOF", "unclosed delimiter"))
}
// Only the exact bounded scalar signature profile is proved here. Any
// unsupported type/effect/borrow syntax fails closed rather than being erased.
fn checked_signature(t: &[Token], args_open: usize, sig_end: usize) -> R<Vec<Tok>> {
    let args_end = match_pair(t, args_open, Tok::LParen, Tok::RParen)?;
    if args_end + 2 != sig_end || t.get(args_end + 1).map(|x| &x.tok) != Some(&Tok::Colon) {
        return Err(err(t[args_open].pos, "E_UNSUPPORTED",
            "contract signatures require explicit ': scalar_type' and no unverified effects"));
    }
    let result = match t.get(args_end + 2).map(|x| &x.tok) {
        Some(Tok::Ident(s)) if matches!(s.as_str(), "int" | "bool" | "string" | "void") => s.clone(),
        _ => return Err(err(t[args_open].pos, "E_UNSUPPORTED",
            "contract signature result must use an admitted scalar type")),
    };
    let args = &t[args_open + 1..args_end];
    let mut canonical = vec![Tok::LParen];
    let mut j = 0;
    while j < args.len() {
        let scalar = |x: &Tok| match x {
            Tok::Ident(s) if matches!(s.as_str(), "int" | "bool" | "string") => Some(s.clone()),
            _ => None,
        };
        let (ty, consumed) = if j + 1 < args.len() {
            if let Some(s) = scalar(&args[j].tok) {
                if matches!(&args[j + 1].tok, Tok::Ident(_)) { (s, 2) }
                else { return Err(err(args[j].pos, "E_UNSUPPORTED", "unproved typed parameter")); }
            } else if j + 2 < args.len() && args[j + 1].tok == Tok::Colon {
                if let Some(s) = scalar(&args[j + 2].tok) { (s, 3) }
                else { return Err(err(args[j].pos, "E_UNSUPPORTED", "unproved typed parameter")); }
            } else {
                return Err(err(args[j].pos, "E_UNSUPPORTED", "unproved parameter ownership/type"));
            }
        } else {
            return Err(err(args[j].pos, "E_UNSUPPORTED", "incomplete typed parameter"));
        };
        if canonical.len() > 1 { canonical.push(Tok::Comma); }
        canonical.push(Tok::Ident(ty));
        j += consumed;
        if j < args.len() {
            if args[j].tok != Tok::Comma {
                return Err(err(args[j].pos, "E_SYNTAX", "missing parameter separator"));
            }
            j += 1;
            if j == args.len() {
                return Err(err(args[j - 1].pos, "E_SYNTAX", "trailing parameter comma"));
            }
        }
    }
    canonical.push(Tok::RParen);
    canonical.push(Tok::Colon);
    canonical.push(Tok::Ident(result));
    Ok(canonical)
}
fn scan_methods(t: &[Token], kind: Kind) -> R<BTreeMap<String, Method>> {
    let mut out = BTreeMap::new();
    let mut i = 0usize;
    while i < t.len() {
        if out.len() >= MAX_MEMBERS { return Err(err(t[i].pos, "E_SOURCE_LIMIT", "too many receiver methods")); }
        if t[i].tok == Tok::LBrace {
            i = match_pair(t, i, Tok::LBrace, Tok::RBrace)? + 1;
            continue;
        }
        let start = i;
        let public = if is(t, i, "pub") { i += 1; true }
            else if is(t, i, "private") { i += 1; false }
            else { matches!(kind, Kind::Trait | Kind::Interface) };
        if is(t, i, "fnc") || is(t, i, "method") { i += 1; }
        // A receive/constructor is not an ordinary method. Skip its entire
        // body; a nested call such as self.ping(...) cannot manufacture a
        // second method slot in the surrounding actor's declaration.
        if is(t, i, "receive") || is(t, i, "constructor") {
            if t.get(i + 1).map(|x| &x.tok) == Some(&Tok::LParen) {
                let params_end = match_pair(t, i + 1, Tok::LParen, Tok::RParen)?;
                let body = (params_end + 1..t.len())
                    .find(|&j| t[j].tok == Tok::LBrace || t[j].tok == Tok::Semi)
                    .ok_or_else(|| err(t[i].pos, "E_EOF", "unterminated receive or constructor"))?;
                i = if t[body].tok == Tok::LBrace {
                    match_pair(t, body, Tok::LBrace, Tok::RBrace)? + 1
                } else { body + 1 };
            } else { i = start + 1; }
            continue;
        }
        if !matches!(t.get(i + 1).map(|x| &x.tok), Some(Tok::LParen)) {
            i = start + 1; continue;
        }
        let name = identifier(t, i)?;
        let at = t[i].pos;
        let args_open = i + 1;
        let args_end = match_pair(t, args_open, Tok::LParen, Tok::RParen)?;
        let mut end = args_end + 1;
        while end < t.len() && t[end].tok != Tok::LBrace && t[end].tok != Tok::Semi {
            if is(t, end, "end") { return Err(err(t[end].pos, "E_UNSUPPORTED", "unsupported end-bodied contract member")); }
            end += 1;
        }
        if end == t.len() { return Err(err(at, "E_EOF", "unfinished method declaration")); }
        let sig = checked_signature(t, args_open, end - 1)?;
        let concrete = t[end].tok == Tok::LBrace;
        if kind == Kind::Interface && concrete { return Err(err(at, "E_IMPL_SIGNATURE", "interface may not define a method body")); }
        if matches!(kind, Kind::Actor | Kind::Struct) && !concrete {
            return Err(err(at, "E_IMPL_MISSING", "concrete owner method has no body"));
        }
        if out.insert(name.clone(), Method { sig, public, concrete, pos: at }).is_some() {
            return Err(err(at, "E_DUPLICATE", format!("duplicate receiver method '{name}'")));
        }
        i = if concrete { match_pair(t, end, Tok::LBrace, Tok::RBrace)? + 1 } else { end + 1 };
    }
    Ok(out)
}
fn scan_mtd(t: &[Token], i: usize) -> R<(String, String, Method, usize)> {
    let pos = t[i].pos;
    let mut j = i;
    let public = if is(t, j, "pub") { j += 1; true } else { false };
    if !is(t, j, "mtd") { return Err(err(pos, "E_SYNTAX", "expected mtd")); }
    j += 1;
    if t.get(j).map(|x| &x.tok) != Some(&Tok::LParen) {
        return Err(err(pos, "E_SYNTAX", "mtd needs exact nominal receiver"));
    }
    let receiver_end = match_pair(t, j, Tok::LParen, Tok::RParen)?;
    let receiver = &t[j + 1..receiver_end];
    let owner = match receiver {
        [Token { tok: Tok::Ident(n), .. }, Token { tok: Tok::Ident(s), .. }] if s == "self" => n.clone(),
        [Token { tok: Tok::Ident(s), .. }, Token { tok: Tok::Colon, .. },
         Token { tok: Tok::Ident(n), .. }] if s == "self" => n.clone(),
        _ => return Err(err(pos, "E_UNSUPPORTED", "only immutable nominal mtd receivers admitted to conformance preflight")),
    };
    j = receiver_end + 1;
    let method_name = identifier(t, j)?;
    j += 1;
    if t.get(j).map(|x| &x.tok) != Some(&Tok::LParen) {
        return Err(err(pos, "E_SYNTAX", "mtd requires arguments"));
    }
    let args_end = match_pair(t, j, Tok::LParen, Tok::RParen)?;
    let mut end = args_end + 1;
    while end < t.len() && t[end].tok != Tok::LBrace && t[end].tok != Tok::Semi { end += 1; }
    if end == t.len() || t[end].tok != Tok::LBrace {
        return Err(err(pos, "E_IMPL_MISSING", "out-of-line mtd requires a checked body"));
    }
    let sig = checked_signature(t, j, end - 1)?;
    let body_end = match_pair(t, end, Tok::LBrace, Tok::RBrace)?;
    Ok((owner, method_name, Method { sig, public, concrete: true, pos }, body_end + 1))
}
/// A safe early rule for actor/struct implements, before the backend may
/// accidentally interpret their methods as ordinary class methods.
pub fn preflight(t: &[Token]) -> R<()> {
    check(t, false)
}

/// Admit a restricted, actor-local receiver ABI only after all same-source
/// interface/trait and nominal method obligations have been checked.
pub(crate) fn validate_actor_lowering(t: &[Token]) -> R<()> {
    check(t, true)
}

fn check(t: &[Token], actor_lowering: bool) -> R<()> {
    // Do not alter existing class-only receiver paths or ordinary programs:
    // the target admission pass is needed solely when an actor/struct header
    // actually declares an implements clause.
    let targeted = t.windows(4).any(|w|
        matches!((&w[0].tok, &w[1].tok, &w[2].tok, &w[3].tok),
            (Tok::Ident(verb), Tok::Ident(kind), Tok::Ident(_), Tok::Ident(clause))
                if (verb == "define" || verb == "declare")
                    && (kind == "actor" || kind == "struct") && clause == "implements"));
    if !targeted { return Ok(()); }
    let mut all = BTreeMap::<String, Owner>::new();
    let mut forwards = BTreeMap::<String, (Kind, Vec<String>, Pos)>::new();
    let mut receivers = Vec::new();
    let mut i = 0usize;
    let mut needs_check = false;
    while i < t.len() {
        // A receiver only exists at file scope. A fake `mtd` inside a
        // function, closure or another brace body must never satisfy a
        // concrete owner's required interface/trait method.
        if t[i].tok == Tok::LBrace {
            i = match_pair(t, i, Tok::LBrace, Tok::RBrace)? + 1;
            continue;
        }
        let begin = i;
        let mut j = i;
        let public = is(t, j, "pub");
        if public { j += 1; }
        if is(t, j, "mtd") {
            let (owner, method, implm, end) = scan_mtd(t, begin)?;
            receivers.push((owner, method, implm));
            i = end; continue;
        }
        let define = is(t, j, "define");
        let declare = is(t, j, "declare");
        if !define && !declare { i += 1; continue; }
        j += 1;
        if is(t, j, "pub") { j += 1; }
        let kind = if is(t, j, "actor") { Kind::Actor }
            else if is(t, j, "struct") { Kind::Struct }
            else if is(t, j, "class") { Kind::Class }
            else if is(t, j, "interface") { Kind::Interface }
            else if is(t, j, "trait") { Kind::Trait }
            else { i += 1; continue; };
        j += 1;
        let owner_name = identifier(t, j)?;
        let pos = t[begin].pos;
        j += 1;
        let mut parents = Vec::new();
        if is(t, j, "extends") {
            j += 1;
            loop {
                parents.push(identifier(t, j)?); j += 1;
                if t.get(j).map(|x| &x.tok) != Some(&Tok::Comma) { break; }
                j += 1;
                if parents.len() > MAX_CONTRACTS { return Err(err(pos, "E_SOURCE_LIMIT", "too many base types")); }
            }
        }
        let mut contracts = Vec::new();
        if is(t, j, "implements") {
            j += 1;
            loop {
                let contract = identifier(t, j)?;
                if contracts.contains(&contract) { return Err(err(t[j].pos, "E_DUPLICATE", "duplicate implements entry")); }
                contracts.push(contract); j += 1;
                if t.get(j).map(|x| &x.tok) != Some(&Tok::Comma) { break; }
                j += 1;
                if contracts.len() >= MAX_CONTRACTS { return Err(err(pos, "E_SOURCE_LIMIT", "too many implemented contracts")); }
            }
        }
        if matches!(kind, Kind::Actor | Kind::Struct) && !contracts.is_empty() {
            needs_check = true;
            if !parents.is_empty() {
                return Err(err(pos, "E_UNSUPPORTED", "actor/struct extends requires separately verified base inheritance"));
            }
        }
        if declare {
            if t.get(j).map(|x| &x.tok) != Some(&Tok::Semi) {
                return Err(err(pos, "E_SYNTAX", "forward nominal declaration requires ';'"));
            }
            if forwards.insert(owner_name.clone(), (kind, contracts, pos)).is_some() {
                return Err(err(pos, "E_DUPLICATE", "duplicate nominal forward declaration"));
            }
            i = j + 1;
        } else {
            if !is(t, j, "as") { return Err(err(pos, "E_SYNTAX", "nominal definition requires 'as'")); }
            j += 1;
            let body_start = j;
            let mut depth = 0usize;
            while j < t.len() {
                if t[j].tok == Tok::LBrace { depth += 1; }
                if t[j].tok == Tok::RBrace {
                    if depth == 0 { return Err(err(t[j].pos, "E_SYNTAX", "unmatched nominal body brace")); }
                    depth -= 1;
                }
                if depth == 0 && is(t, j, "end") { break; }
                if t[j].tok == Tok::Eof { break; }
                j += 1;
            }
            if j >= t.len() || t[j].tok == Tok::Eof {
                return Err(err(pos, "E_EOF", "unclosed nominal definition"));
            }
            let methods = if matches!(kind, Kind::Actor | Kind::Struct | Kind::Trait | Kind::Interface) {
                scan_methods(&t[body_start..j], kind)?
            } else { BTreeMap::new() };
            if all.insert(owner_name.clone(), Owner { kind, pos, contracts, methods }).is_some() {
                return Err(err(pos, "E_DUPLICATE", "duplicate nominal definition"));
            }
            i = j + 1;
        }
        if all.len() + forwards.len() > MAX_TYPES {
            return Err(err(pos, "E_SOURCE_LIMIT", "too many nominal declarations"));
        }
    }
    if !needs_check { return Ok(()); }
    for (name, (kind, contracts, pos)) in &forwards {
        if let Some(owner) = all.get(name) {
            if *kind != owner.kind || *contracts != owner.contracts {
                return Err(err(*pos, "E_IMPL_SIGNATURE", "forward and definition implements clauses/kinds disagree"));
            }
        } else if matches!(kind, Kind::Actor | Kind::Struct) && !contracts.is_empty() {
            return Err(err(*pos, "E_IMPL_UNRESOLVED", format!("implemented forward owner '{name}' needs complete definition")));
        }
    }
    for (name, method_name, method) in receivers {
        if let Some(owner) = all.get_mut(&name) {
            if matches!(owner.kind, Kind::Actor | Kind::Struct)
                && owner.methods.insert(method_name.clone(), method.clone()).is_some()
            {
                return Err(err(method.pos, "E_DUPLICATE", "duplicate inline/out-of-line receiver method"));
            }
        }
    }
    for (name, owner) in &all {
        if !matches!(owner.kind, Kind::Actor | Kind::Struct) || owner.contracts.is_empty() { continue; }
        let mut required = BTreeMap::<String, Vec<Tok>>::new();
        let mut defaults = BTreeMap::<String, Vec<Method>>::new();
        for contract_name in &owner.contracts {
            let contract = all.get(contract_name).ok_or_else(||
                err(owner.pos, "E_IMPL_UNRESOLVED", format!("unknown implemented type '{contract_name}'")))?;
            if !matches!(contract.kind, Kind::Interface | Kind::Trait) {
                return Err(err(owner.pos, "E_IMPL_KIND", format!("'{contract_name}' is not a trait or interface")));
            }
            for (method_name, method) in &contract.methods {
                if !method.concrete {
                    if let Some(old) = required.insert(method_name.clone(), method.sig.clone()) {
                        if old != method.sig {
                            return Err(err(method.pos, "E_IMPL_SIGNATURE", "conflicting abstract method signatures"));
                        }
                    }
                } else {
                    defaults.entry(method_name.clone()).or_default().push(method.clone());
                }
            }
        }
        for (method_name, choices) in &defaults {
            if choices.len() > 1 && !owner.methods.contains_key(method_name) {
                return Err(err(owner.pos, "E_IMPL_CONFLICT",
                    format!("conflicting trait defaults for '{name}.{method_name}'")));
            }
            if let Some(method) = owner.methods.get(method_name) {
                if choices.iter().any(|candidate| candidate.sig != method.sig) {
                    return Err(err(method.pos, "E_IMPL_SIGNATURE", "trait default override signature mismatch"));
                }
            }
        }
        for (method_name, signature) in required {
            let selected = owner.methods.get(&method_name).or_else(||
                defaults.get(&method_name).and_then(|xs| if xs.len() == 1 { xs.first() } else { None }));
            let method = selected.ok_or_else(||
                err(owner.pos, "E_IMPL_MISSING", format!("{name} is missing '{method_name}'")))?;
            if !method.public {
                return Err(err(method.pos, "E_IMPL_VISIBILITY", format!("{name}.{method_name} must be public")));
            }
            if method.sig != signature {
                return Err(err(method.pos, "E_IMPL_SIGNATURE", format!("{name}.{method_name} signature mismatch")));
            }
        }
    }
    if actor_lowering {
        // This narrow path uses existing actor-local receive/method emission,
        // never direct ActorRef dispatch or class-shaped struct storage.
        // Trait defaults are not copied into actor methods in this milestone;
        // reject their reliance rather than silently dropping a body.
        for owner in all.values().filter(|o| o.kind == Kind::Actor && !o.contracts.is_empty()) {
            for name in &owner.contracts {
                let c = &all[name];
                if c.kind == Kind::Trait && c.methods.values().any(|m| m.concrete) {
                    return Err(err(owner.pos, "E_UNSUPPORTED",
                        "trait default actor receiver bodies need checked actor-local lowering"));
                }
            }
        }
        if all.values().any(|o| o.kind == Kind::Struct && !o.contracts.is_empty()) {
            return Err(err(t.first().map_or(Pos::default(), |x| x.pos), "E_UNSUPPORTED",
                "struct value receiver ABI is not yet verified"));
        }
        return Ok(());
    }
    // Outside the explicitly validated actor-local profile, keep the original
    // production gate closed for both actors and structs.
    Err(err(t.first().map_or(Pos::default(), |x| x.pos), "E_UNSUPPORTED",
        "actor/struct implements contracts validated for bounded signatures; guest lowering requires a verified actor/value ABI"))
}
