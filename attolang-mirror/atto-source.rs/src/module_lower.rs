//! Bounded single-source nominal-module lowering for the browser compiler.
//!
//! This is NOT a cross-file importer or a runtime module object. Only direct
//! module-qualified calls to declared file-local functions are rewritten to
//! unforgeable names before the ordinary typed frontend and Wasm emitter.
//! Private members are inaccessible from other scopes, and any unsupported
//! declaration / first-class module expression fails without a guest artifact.
use std::collections::BTreeMap;

use crate::diag::{Diagnostic, Pos};
use crate::lexer::{Tok, Token};

type R<T> = Result<T, Diagnostic>;
const PREFIX: &str = "__atto_internal_module_";
const MAX_MODULES: usize = 64;
const MAX_MEMBERS: usize = 128;
const MAX_NAME: usize = 64;

#[derive(Clone)]
struct Member {
    generated: String,
    public: bool,
}
#[derive(Default)]
struct Module {
    members: BTreeMap<String, Member>,
}
/// Checked file-local module symbol collected before lowering removes its body.
/// This is NOT a cross-file export or linker grant.
#[derive(Clone, Debug)]
pub struct ModuleDecl {
    pub name: String,
    pub inline_public: bool,
    pub pos: Pos,
}
pub struct Lowered {
    pub tokens: Vec<Token>,
    pub modules: Vec<ModuleDecl>,
}

fn word(tokens: &[Token], i: usize, expected: &str) -> bool {
    matches!(tokens.get(i).map(|t| &t.tok), Some(Tok::Ident(s)) if s == expected)
}
fn ident(tokens: &[Token], i: usize, what: &str) -> R<String> {
    match tokens.get(i) {
        Some(Token {
            tok: Tok::Ident(s),
            pos: _,
        }) if !s.is_empty() && s.len() <= MAX_NAME && !matches!(s.as_str(), "as" | "end" | "pub" | "fnc" | "define" | "module") => {
            Ok(s.clone())
        }
        Some(token) => Err(Diagnostic::new(
            token.pos,
            "E_SYNTAX",
            format!("invalid {what} (maximum {MAX_NAME} UTF-8 bytes)"),
        )),
        None => Err(Diagnostic::new(Pos { line: 1, col: 1 }, "E_EOF", format!("missing {what}"))),
    }
}
fn unsupported(pos: Pos, message: &str) -> Diagnostic {
    Diagnostic::new(pos, "E_UNSUPPORTED", message)
}

/// Return token text with file-local declarations flattened, retaining each
/// original token's source position for normal parser/typechecker diagnostics.
pub fn lower(toks: Vec<Token>) -> R<Vec<Token>> {
    Ok(lower_with_manifest(toks)?.tokens)
}

/// Returns exact checked module declarations alongside flattened member tokens,
/// so exports cannot accidentally bind an imported or forged module name.
pub fn lower_with_manifest(toks: Vec<Token>) -> R<Lowered> {
    if !toks.iter().any(|t| matches!(&t.tok, Tok::Ident(s) if s == "module")) {
        return Ok(Lowered {
            tokens: toks,
            modules: vec![],
        });
    }
    // Mangled declarations cannot be impersonated by user code, including
    // declarations that occur before the modules themselves.
    for token in &toks {
        if let Tok::Ident(name) = &token.tok {
            if name.starts_with(PREFIX) {
                return Err(Diagnostic::new(
                    token.pos,
                    "E_RESERVED",
                    "reserved internal module symbol cannot appear in source",
                ));
            }
        }
    }

    let mut modules = BTreeMap::<String, Module>::new();
    let mut declarations: Vec<ModuleDecl> = vec![];
    let mut expanded = Vec::<(Token, Option<String>)>::new();
    let (mut i, mut braces) = (0usize, 0usize);

    while i < toks.len() {
        let inline_public = braces == 0 && word(&toks, i, "define") && word(&toks, i + 1, "pub") && word(&toks, i + 2, "module");
        if braces == 0 && word(&toks, i, "define") && (word(&toks, i + 1, "module") || inline_public) {
            let pos = toks[i].pos;
            let name_index = if inline_public { i + 3 } else { i + 2 };
            if modules.len() >= MAX_MODULES {
                return Err(Diagnostic::new(pos, "E_SOURCE_LIMIT", "too many modules"));
            }
            let owner = ident(&toks, name_index, "module name")?;
            if !word(&toks, name_index + 1, "as") {
                return Err(Diagnostic::new(
                    toks[name_index].pos,
                    "E_SYNTAX",
                    "file-local module must start with 'define [pub] module Name as'",
                ));
            }
            if modules.contains_key(&owner) {
                return Err(Diagnostic::new(pos, "E_DUPLICATE", format!("duplicate module '{owner}'")));
            }
            i = name_index + 2;
            let mut module = Module::default();
            while !word(&toks, i, "end") {
                let Some(current) = toks.get(i) else {
                    return Err(Diagnostic::new(pos, "E_EOF", "unterminated module"));
                };
                if current.tok == Tok::Eof {
                    return Err(Diagnostic::new(pos, "E_EOF", "unterminated module"));
                }
                if module.members.len() >= MAX_MEMBERS {
                    return Err(Diagnostic::new(current.pos, "E_SOURCE_LIMIT", "module member budget exceeded"));
                }
                let member_start = i;
                let public = word(&toks, i, "pub");
                if public {
                    i += 1;
                }
                if !word(&toks, i, "fnc") {
                    return Err(unsupported(
                        toks[i].pos,
                        "file-local module profile permits only fnc member declarations",
                    ));
                }
                i += 1;
                let name_at = i;
                let name = ident(&toks, i, "module function name")?;
                i += 1;
                if toks.get(i).map(|t| &t.tok) != Some(&Tok::LParen) {
                    return Err(unsupported(
                        toks[i.min(toks.len() - 1)].pos,
                        "generic or non-function module members need a full module linker",
                    ));
                }
                if module.members.contains_key(&name) {
                    return Err(Diagnostic::new(
                        toks[name_at].pos,
                        "E_DUPLICATE",
                        format!("duplicate module member '{owner}.{name}'"),
                    ));
                }
                // Only brace-bodied methods in this first executable module
                // profile: no false assumption about nested end-delimiters.
                let mut header = i;
                while header < toks.len()
                    && toks[header].tok != Tok::LBrace
                    && toks[header].tok != Tok::Eof
                    && toks[header].tok != Tok::RBrace
                    && !word(&toks, header, "end")
                {
                    header += 1;
                }
                if toks.get(header).map(|t| &t.tok) != Some(&Tok::LBrace) {
                    return Err(unsupported(toks[i].pos, "only braced module function bodies are executable"));
                }
                let (mut close, mut depth) = (header + 1, 1usize);
                while depth > 0 {
                    match toks.get(close).map(|t| &t.tok) {
                        Some(Tok::LBrace) => depth += 1,
                        Some(Tok::RBrace) => depth -= 1,
                        Some(Tok::Eof) | None => {
                            return Err(Diagnostic::new(toks[header].pos, "E_EOF", "unterminated module function body"))
                        }
                        _ => {}
                    }
                    if depth > 128 {
                        return Err(Diagnostic::new(
                            toks[header].pos,
                            "E_NESTING",
                            "module function nesting exceeds 128",
                        ));
                    }
                    close += 1;
                }
                let generated = format!("{PREFIX}{}_{}_{}_{}", owner.len(), owner, name.len(), name);
                for (index, original) in toks[member_start..close].iter().enumerate() {
                    let mut token = original.clone();
                    if member_start + index == name_at {
                        token.tok = Tok::Ident(generated.clone());
                    }
                    expanded.push((token, Some(owner.clone())));
                }
                module.members.insert(name, Member { generated, public });
                i = close;
            }
            i += 1; // consume module's own 'end'
            declarations.push(ModuleDecl {
                name: owner.clone(),
                inline_public,
                pos,
            });
            modules.insert(owner, module);
            continue;
        }
        match toks[i].tok {
            Tok::LBrace => braces += 1,
            Tok::RBrace => braces = braces.saturating_sub(1),
            _ => {}
        }
        expanded.push((toks[i].clone(), None));
        i += 1;
    }
    if modules.is_empty() {
        return Ok(Lowered {
            tokens: toks,
            modules: vec![],
        }); // Normal class/actor syntax remains untouched.
    }

    let mut out = Vec::with_capacity(expanded.len());
    let mut i = 0;
    while i < expanded.len() {
        if let Tok::Ident(owner) = &expanded[i].0.tok {
            if let Some(module) = modules.get(owner) {
                let pos = expanded[i].0.pos;
                if i > 0 && expanded[i - 1].0.tok == Tok::Dot {
                    return Err(unsupported(pos, "module cannot be a member of a runtime object"));
                }
                let member = match (
                    expanded.get(i + 1).map(|x| &x.0.tok),
                    expanded.get(i + 2).map(|x| &x.0.tok),
                    expanded.get(i + 3).map(|x| &x.0.tok),
                ) {
                    (Some(Tok::Dot), Some(Tok::Ident(member)), Some(Tok::LParen)) => member,
                    _ => {
                        return Err(unsupported(
                            pos,
                            "file-local modules support direct qualified calls only, not module values or shadowed bindings",
                        ))
                    }
                };
                let found = module
                    .members
                    .get(member)
                    .ok_or_else(|| Diagnostic::new(pos, "E_UNBOUND", format!("unknown module member '{owner}.{member}'")))?;
                if !found.public && expanded[i].1.as_deref() != Some(owner.as_str()) {
                    return Err(Diagnostic::new(
                        pos,
                        "E_PRIVATE",
                        format!("private module member '{owner}.{member}' is not externally accessible"),
                    ));
                }
                out.push(Token {
                    tok: Tok::Ident(found.generated.clone()),
                    pos,
                });
                i += 3;
                continue;
            }
        }
        out.push(expanded[i].0.clone());
        i += 1;
    }
    Ok(Lowered {
        tokens: out,
        modules: declarations,
    })
}
