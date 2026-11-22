//! Fail-closed, parse-only admission for type declarations.
//! This validates headers and local alias contracts. It intentionally does NOT
//! lower tagged unions, interfaces or traits to executable WebAssembly.
use crate::diag::{Diagnostic, Pos};
use crate::lexer::{lex, Tok, Token};
use crate::parser::{is_reserved, MAX_FUNCS};
use std::collections::{BTreeMap, BTreeSet};

type R<T> = Result<T, Diagnostic>;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TypeDeclarationKind { Declare, Define }

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum TypeExpression {
    Name(String),
    Union(Vec<String>),
    Interface(String),
    Trait(String),
}

#[derive(Clone, Debug)]
pub struct TypeDeclaration {
    pub name: String,
    pub kind: TypeDeclarationKind,
    pub expression: TypeExpression,
    pub pos: Pos,
}

struct Cursor<'a> { toks: &'a [Token], i: usize }
impl Cursor<'_> {
    fn tok(&self) -> &Tok { &self.toks[self.i.min(self.toks.len() - 1)].tok }
    fn pos(&self) -> Pos { self.toks[self.i.min(self.toks.len() - 1)].pos }
    fn word(&self) -> Option<&str> {
        match self.tok() { Tok::Ident(s) => Some(s), _ => None }
    }
    fn next(&mut self) { if self.i + 1 < self.toks.len() { self.i += 1; } }
    fn take(&mut self, tok: &Tok) -> bool {
        if self.tok() == tok { self.next(); true } else { false }
    }
    fn take_word(&mut self, word: &str) -> bool {
        if self.word() == Some(word) { self.next(); true } else { false }
    }
    fn err(&self, code: &'static str, message: impl Into<String>) -> Diagnostic {
        Diagnostic::new(self.pos(), code, message)
    }
    fn expect(&mut self, tok: &Tok, label: &str) -> R<()> {
        if self.take(tok) { Ok(()) } else { Err(self.err("E_SYNTAX", label)) }
    }
    fn expect_word(&mut self, word: &str) -> R<()> {
        if self.take_word(word) { Ok(()) }
        else { Err(self.err("E_SYNTAX", format!("expected '{word}'"))) }
    }
    fn name(&mut self, defining: bool) -> R<String> {
        let n = match self.tok() {
            Tok::Ident(s) => s.clone(),
            _ => return Err(self.err("E_SYNTAX", "expected a type identifier")),
        };
        if defining && (is_reserved(&n) ||
            matches!(n.as_str(), "declare" | "define" | "bool" | "boolean" | "int" |
                "float" | "f64" | "string" | "String" | "void" | "interface" | "trait")) {
            return Err(self.err("E_SYNTAX", format!("invalid type declaration name '{n}'")));
        }
        if !defining && (is_reserved(&n) &&
            !matches!(n.as_str(), "bool" | "boolean" | "int" | "float" | "f64" |
                "string" | "String" | "void")) {
            return Err(self.err("E_SYNTAX", format!("invalid type reference '{n}'")));
        }
        self.next();
        Ok(n)
    }
    fn expression(&mut self, kind: TypeDeclarationKind) -> R<TypeExpression> {
        let first = match self.word() {
            Some("interface") | Some("trait") => {
                if kind != TypeDeclarationKind::Define {
                    return Err(self.err("E_SYNTAX",
                        "inline nominal bodies require 'define type ... as'"));
                }
                let is_trait = self.take_word("trait");
                if !is_trait { self.expect_word("interface")?; }
                let nominal = self.name(true)?;
                self.expect(&Tok::LBrace, "expected '{' after inline nominal name")?;
                if !self.take(&Tok::RBrace) {
                    return Err(self.err("E_UNSUPPORTED",
                        "nonempty inline interface/trait bodies need verified member parsing"));
                }
                if is_trait { TypeExpression::Trait(nominal) }
                else { TypeExpression::Interface(nominal) }
            }
            _ => TypeExpression::Name(self.name(false)?),
        };
        if !self.take(&Tok::Pipe) { return Ok(first); }
        let mut arms = match first {
            TypeExpression::Name(n) => vec![n],
            _ => return Err(self.err("E_UNSUPPORTED",
                "union of inline nominal bodies is not supported by this preflight")),
        };
        loop {
            let n = self.name(false)?;
            if arms.contains(&n) {
                return Err(self.err("E_DUPLICATE",
                    format!("duplicate union member '{n}'")));
            }
            arms.push(n);
            if !self.take(&Tok::Pipe) { break; }
        }
        Ok(TypeExpression::Union(arms))
    }
}

fn normalized(e: &TypeExpression) -> String {
    match e {
        TypeExpression::Name(n) | TypeExpression::Interface(n) |
            TypeExpression::Trait(n) => format!("name:{n}"),
        TypeExpression::Union(items) => {
            let mut sorted = items.clone();
            sorted.sort();
            format!("union:{}", sorted.join("|"))
        }
    }
}

fn verify(headers: &[TypeDeclaration]) -> R<()> {
    let mut slots: BTreeMap<&str, (Option<&TypeDeclaration>, Option<&TypeDeclaration>)> =
        BTreeMap::new();
    let mut nominals = BTreeMap::<&str, (&str, &'static str)>::new();
    for h in headers {
        let slot = slots.entry(&h.name).or_default();
        let selected = match h.kind {
            TypeDeclarationKind::Declare => &mut slot.0,
            TypeDeclarationKind::Define => &mut slot.1,
        };
        if selected.is_some() {
            return Err(Diagnostic::new(h.pos, "E_DUPLICATE",
                format!("duplicate {:?} type '{}'", h.kind, h.name)));
        }
        *selected = Some(h);
        let (nominal, kind) = match &h.expression {
            TypeExpression::Interface(n) => (n.as_str(), "interface"),
            TypeExpression::Trait(n) => (n.as_str(), "trait"),
            _ => continue,
        };
        if let Some((prev, _)) = nominals.insert(nominal, (&h.name, kind)) {
            return Err(Diagnostic::new(h.pos, "E_DUPLICATE",
                format!("nominal type '{nominal}' is already defined by '{prev}'")));
        }
    }
    for (name, (decl, def)) in &slots {
        if let (Some(d), Some(f)) = (decl, def) {
            if normalized(&d.expression) != normalized(&f.expression) {
                return Err(Diagnostic::new(f.pos, "E_SIGNATURE",
                    format!("definition of '{name}' does not match its declaration")));
            }
        }
    }
    for (nominal, (owner, _)) in &nominals {
        if *nominal != *owner && slots.contains_key(nominal) {
            return Err(Diagnostic::new(Pos::default(), "E_DUPLICATE",
                format!("inline nominal '{nominal}' conflicts with a type alias")));
        }
    }
    // Check all local alias dependencies, including references inside unions.
    // Nominal definitions terminate the graph; unknown imported/forward names
    // are intentionally left unresolved for the later linker.
    let mut aliases: BTreeMap<&str, Vec<&str>> = BTreeMap::new();
    for (name, (decl, def)) in &slots {
        let expr = def.or(*decl).expect("type group has a declaration");
        let edges = match &expr.expression {
            TypeExpression::Name(n) => vec![n.as_str()],
            TypeExpression::Union(items) => items.iter().map(String::as_str).collect(),
            TypeExpression::Interface(_) | TypeExpression::Trait(_) => Vec::new(),
        };
        aliases.insert(name, edges);
    }
    // Iterative DFS avoids recursion and remains bounded by MAX_FUNCS and
    // the lexer token budget, even with adversarial deep alias chains.
    for root in aliases.keys() {
        let mut visited = BTreeSet::new();
        let mut pending = vec![*root];
        while let Some(at) = pending.pop() {
            if !visited.insert(at) { continue; }
            if let Some(edges) = aliases.get(at) {
                for next in edges {
                    if next == root {
                        return Err(Diagnostic::new(Pos::default(), "E_TYPE_CYCLE",
                            format!("cyclic type alias involving '{root}'")));
                    }
                    if aliases.contains_key(next) {
                        pending.push(next);
                    }
                }
            }
        }
    }
    Ok(())
}

/// Detect a file-scope type declaration without being fooled by braced bodies.
pub fn contains_type_declaration(toks: &[Token]) -> bool {
    let mut braces = 0usize;
    for (i, token) in toks.iter().enumerate() {
        match &token.tok {
            Tok::LBrace => braces = braces.saturating_add(1),
            Tok::RBrace => braces = braces.saturating_sub(1),
            Tok::Ident(s) if braces == 0 && (s == "define" || s == "declare") => {
                if matches!(toks.get(i + 1).map(|t| &t.tok),
                    Some(Tok::Ident(next)) if next == "type") {
                    return true;
                }
            }
            _ => {}
        }
    }
    false
}

/// Type-only source preflight. Valid syntax is NOT proof of runtime support.
pub fn parse_source(source: &str) -> R<Vec<TypeDeclaration>> {
    if source.len() > crate::SOURCE_CAP {
        return Err(Diagnostic::new(Pos::default(), "E_SOURCE_LIMIT",
            "type declaration source exceeds compiler source limit"));
    }
    let toks = lex(source)?;
    parse_tokens(&toks)
}

pub fn parse_tokens(toks: &[Token]) -> R<Vec<TypeDeclaration>> {
    if toks.is_empty() {
        return Err(Diagnostic::new(Pos::default(), "E_EOF", "empty token stream"));
    }
    let mut c = Cursor { toks, i: 0 };
    let mut headers = Vec::new();
    while *c.tok() != Tok::Eof {
        if headers.len() >= MAX_FUNCS {
            return Err(c.err("E_SOURCE_LIMIT", "too many type declarations"));
        }
        let pos = c.pos();
        let kind = if c.take_word("define") {
            TypeDeclarationKind::Define
        } else if c.take_word("declare") {
            TypeDeclarationKind::Declare
        } else {
            return Err(c.err("E_SYNTAX",
                "type preflight requires only define type / declare type declarations"));
        };
        c.expect_word("type")?;
        let name = c.name(true)?;
        match kind {
            TypeDeclarationKind::Define => c.expect_word("as")?,
            TypeDeclarationKind::Declare => c.expect(&Tok::Assign,
                "declare type requires '=' and not 'as'")?,
        }
        let expression = c.expression(kind)?;
        let braced = matches!(&expression, TypeExpression::Interface(_) | TypeExpression::Trait(_));
        if !c.take(&Tok::Semi) && *c.tok() != Tok::Eof &&
            !(braced && matches!(c.word(), Some("define" | "declare"))) {
            return Err(c.err("E_SYNTAX",
                "expected ';' between unbraced type declarations"));
        }
        headers.push(TypeDeclaration { name, kind, expression, pos });
    }
    if headers.is_empty() {
        return Err(Diagnostic::new(Pos::default(), "E_SYNTAX",
            "expected at least one type declaration"));
    }
    verify(&headers)?;
    Ok(headers)
}
