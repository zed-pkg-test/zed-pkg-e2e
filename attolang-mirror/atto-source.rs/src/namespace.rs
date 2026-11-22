//! Deterministic file-scope namespace headers.
//!
//! Folder names come only from a supplied *relative logical source path*;
//! never from host-specific absolute paths, runtime reflection, or the CWD.
//! This normalization is syntax-only: it does not make external imports linkable.
use std::path::{Component, Path};

use crate::diag::{Diagnostic, Pos};
use crate::lexer::{Tok, Token};
use crate::parser::is_reserved;

type R<T> = Result<T, Diagnostic>;
const MAX_NAMESPACE_DEPTH: usize = 32;

fn word(t: &[Token], i: usize, name: &str) -> bool {
    matches!(t.get(i).map(|x| &x.tok), Some(Tok::Ident(s)) if s == name)
}

fn error(pos: Pos, code: &'static str, message: impl Into<String>) -> Diagnostic {
    Diagnostic::new(pos, code, message)
}

fn is_header(t: &[Token], i: usize) -> bool {
    word(t, i, "use")
        && (word(t, i + 1, "namespace")
            || (word(t, i + 1, "default") && word(t, i + 2, "namespace")))
}

fn identifier(value: &str) -> bool {
    let mut chars = value.chars();
    matches!(chars.next(), Some(c) if c.is_ascii_alphabetic() || c == '_')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
        && !is_reserved(value)
}

fn folder_names(path: Option<&Path>, depth: usize, pos: Pos) -> R<Vec<String>> {
    let path = path.ok_or_else(|| error(pos, "E_NAMESPACE_PATH",
        "default namespace requires a package-relative source path; source-only compilation cannot infer a folder"))?;
    let mut parts = Vec::new();
    for component in path.components() {
        match component {
            Component::Normal(name) => {
                let name = name.to_str().ok_or_else(|| error(pos, "E_NAMESPACE_PATH",
                    "source path contains a non-UTF-8 component"))?;
                parts.push(name);
            }
            Component::CurDir => {}
            _ => return Err(error(pos, "E_NAMESPACE_PATH",
                "namespace paths must be package-relative without parent traversal")),
        }
    }
    if parts.last().is_none_or(|filename| !filename.ends_with(".atto")) {
        return Err(error(pos, "E_NAMESPACE_PATH",
            "namespace resolution requires a logical .atto source file"));
    }
    if parts.len() <= depth {
        return Err(error(pos, "E_NAMESPACE_DEPTH",
            format!("namespace depth {depth} exceeds available source directories")));
    }
    let names = &parts[parts.len() - depth - 1..parts.len() - 1];
    for name in names {
        if !identifier(name) {
            return Err(error(pos, "E_NAMESPACE_PATH",
                format!("folder name '{name}' is not a valid namespace identifier")));
        }
    }
    Ok(names.iter().map(|name| (*name).to_owned()).collect())
}

/// Validate and normalize a file-level namespace header. For the derived form,
/// replace it with the identical explicit namespace header that the target
/// parser already understands. Keep the caller's original token positions.
pub fn normalize(t: &[Token], logical_source: Option<&Path>) -> R<Vec<Token>> {
    if t.is_empty() {
        return Ok(Vec::new());
    }
    let mut output = t.to_vec();
    let mut seen = false;
    let mut braces = 0usize;
    let mut i = 0usize;
    while i < t.len() {
        if braces == 0 && is_header(t, i) {
            let pos = t[i].pos;
            if seen {
                return Err(error(pos, "E_DUPLICATE",
                    "only one namespace declaration is allowed per source file"));
            }
            if i != 0 {
                return Err(error(pos, "E_SYNTAX",
                    "namespace declaration must precede all imports and declarations"));
            }
            seen = true;
            let derived = word(t, i + 1, "default");
            let mut cursor = i + if derived { 3 } else { 2 };
            let names = if derived {
                let mut depth = 1usize;
                if matches!(t.get(cursor).map(|x| &x.tok), Some(Tok::LParen)) {
                    cursor += 1;
                    match t.get(cursor).map(|x| &x.tok) {
                        Some(Tok::Int(Some(value)))
                            if *value > 0 && *value <= MAX_NAMESPACE_DEPTH as u64 => {
                                depth = *value as usize;
                            }
                        _ => return Err(error(pos, "E_NAMESPACE_DEPTH",
                            "namespace depth must be an integer between 1 and 32")),
                    }
                    cursor += 1;
                    if !matches!(t.get(cursor).map(|x| &x.tok), Some(Tok::RParen)) {
                        return Err(error(pos, "E_SYNTAX",
                            "expected ')' after namespace depth"));
                    }
                    cursor += 1;
                }
                folder_names(logical_source, depth, pos)?
            } else {
                let mut names = Vec::new();
                loop {
                    match t.get(cursor).map(|x| &x.tok) {
                        Some(Tok::Ident(name)) if identifier(name) => names.push(name.clone()),
                        _ => return Err(error(pos, "E_SYNTAX",
                            "namespace requires a valid identifier")),
                    }
                    cursor += 1;
                    if !matches!(t.get(cursor).map(|x| &x.tok), Some(Tok::Dot)) {
                        break;
                    }
                    cursor += 1;
                    if names.len() >= MAX_NAMESPACE_DEPTH {
                        return Err(error(pos, "E_NAMESPACE_DEPTH",
                            "namespace has more than 32 components"));
                    }
                }
                names
            };
            if !matches!(t.get(cursor).map(|x| &x.tok), Some(Tok::Semi)) {
                return Err(error(pos, "E_SYNTAX", "expected ';' after namespace declaration"));
            }
            cursor += 1;
            let mut header = vec![
                Token { tok: Tok::Ident("use".into()), pos },
                Token { tok: Tok::Ident("namespace".into()), pos },
            ];
            for (j, name) in names.into_iter().enumerate() {
                if j != 0 {
                    header.push(Token { tok: Tok::Dot, pos });
                }
                header.push(Token { tok: Tok::Ident(name), pos });
            }
            header.push(Token { tok: Tok::Semi, pos });
            output.splice(i..cursor, header);
            i = cursor;
            continue;
        }
        match t[i].tok {
            Tok::LBrace => braces += 1,
            Tok::RBrace => braces = braces.saturating_sub(1),
            _ => {}
        }
        i += 1;
    }
    Ok(output)
}
