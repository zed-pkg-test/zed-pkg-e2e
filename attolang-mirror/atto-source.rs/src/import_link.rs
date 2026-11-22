//! Bounded first executable relative-file import profile.
//! File loading is caller supplied, without compiler network or filesystem access.
//! Only one-hop braced functions are admitted; all other imports fail closed.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Component, Path, PathBuf};
use crate::diag::{Diagnostic, Pos};
use crate::lexer::{self, Tok, Token};
use crate::parser::Parser;

type R<T> = Result<T, Diagnostic>;
const RESERVED: &str = "__atto_file_import_";
const MAX_FILES: usize = 16;
const MAX_BINDINGS: usize = 256;
const MAX_TOKENS: usize = 32768;

fn word(t: Option<&Token>, name: &str) -> bool {
    matches!(t.map(|x| &x.tok), Some(Tok::Ident(s)) if s == name)
}
fn diagnostic(pos: Pos, code: &'static str, msg: impl Into<String>) -> Diagnostic {
    Diagnostic::new(pos, code, msg)
}
fn take_ident(tokens: &[Token], i: &mut usize, pos: Pos) -> R<String> {
    let Some(Token { tok: Tok::Ident(name), .. }) = tokens.get(*i) else {
        return Err(diagnostic(pos, "E_SYNTAX", "expected identifier"));
    };
    if matches!(name.as_str(), "from" | "as" | "pkg" | "import" | "pub" | "fnc") {
        return Err(diagnostic(pos, "E_SYNTAX", "reserved import identifier"));
    }
    *i += 1;
    Ok(name.clone())
}
fn require_word(t: &[Token], i: &mut usize, s: &str, pos: Pos) -> R<()> {
    if !word(t.get(*i), s) {
        return Err(diagnostic(pos, "E_SYNTAX", format!("expected '{s}' in import")));
    }
    *i += 1;
    Ok(())
}
fn token(tok: Tok, pos: Pos) -> Token { Token { tok, pos } }
fn ident(s: &str, pos: Pos) -> Token { token(Tok::Ident(s.to_owned()), pos) }

fn import_path(source_file: &Path, value: &str, pos: Pos) -> R<PathBuf> {
    if !(value.starts_with("./") || value.starts_with("../")) || value.contains('\\') || value.contains('\0') {
        return Err(diagnostic(pos, "E_IMPORT_PATH", "file import requires a relative ./ or ../ path"));
    }
    let mut path = PathBuf::new();
    for c in source_file.parent().unwrap_or_else(|| Path::new("")).components()
        .chain(Path::new(value).components()) {
        match c {
            Component::CurDir => {}
            Component::Normal(s) => path.push(s),
            Component::ParentDir if path.pop() => {}
            Component::ParentDir => return Err(diagnostic(pos, "E_IMPORT_PATH", "import escapes project root")),
            Component::Prefix(_) | Component::RootDir => {
                return Err(diagnostic(pos, "E_IMPORT_PATH", "absolute import paths are forbidden"));
            }
        }
    }
    if path.extension().and_then(|s| s.to_str()) != Some("atto") {
        return Err(diagnostic(pos, "E_IMPORT_PATH", "imports must target .atto files"));
    }
    Ok(path)
}
#[derive(Clone)]
struct Import {
    path: PathBuf,
    namespace: Option<String>,
    names: Vec<(String, String)>,
    pos: Pos,
}
struct FileUnit {
    module: String,
    funcs: BTreeMap<String, bool>,
    tokens: Vec<Token>,
}
struct Binding {
    module: String,
    export: Option<String>,
}
fn imports(toks: &[Token], logical: &Path) -> R<(Vec<Import>, Vec<Token>)> {
    let mut result = Vec::new();
    let mut i = 0;
    while word(toks.get(i), "import") {
        let pos = toks[i].pos;
        i += 1;
        let mut namespace = None;
        let mut names = Vec::new();
        if toks.get(i).is_some_and(|t| t.tok == Tok::Star) {
            i += 1;
            require_word(toks, &mut i, "as", pos)?;
            namespace = Some(take_ident(toks, &mut i, pos)?);
        } else {
            loop {
                if names.len() >= MAX_BINDINGS {
                    return Err(diagnostic(pos, "E_SOURCE_LIMIT", "too many imported names"));
                }
                let exported = take_ident(toks, &mut i, pos)?;
                if toks.get(i).is_some_and(|t| t.tok == Tok::Dot) {
                    return Err(diagnostic(pos, "E_UNSUPPORTED", "nested exported modules not yet linked"));
                }
                let local = if word(toks.get(i), "as") {
                    i += 1;
                    take_ident(toks, &mut i, pos)?
                } else { exported.clone() };
                names.push((exported, local));
                if toks.get(i).is_some_and(|t| t.tok == Tok::Comma) { i += 1; }
                else { break; }
            }
        }
        require_word(toks, &mut i, "from", pos)?;
        if word(toks.get(i), "pkg") {
            return Err(diagnostic(pos, "E_UNSUPPORTED", "package imports require a package resolver"));
        }
        let value = match toks.get(i).map(|t| &t.tok) {
            Some(Tok::Str(s)) => s,
            _ => return Err(diagnostic(pos, "E_SYNTAX", "expected quoted source file")),
        };
        let path = import_path(logical, value, pos)?;
        i += 1;
        if toks.get(i).is_none_or(|t| t.tok != Tok::Semi) {
            return Err(diagnostic(pos, "E_SYNTAX", "expected ';' after file import"));
        }
        i += 1;
        result.push(Import { path, namespace, names, pos });
        if result.len() > MAX_BINDINGS {
            return Err(diagnostic(pos, "E_SOURCE_LIMIT", "too many import declarations"));
        }
    }
    Ok((result, toks[i..].to_vec()))
}

fn functions(tokens: &[Token]) -> R<(BTreeMap<String, bool>, BTreeSet<usize>)> {
    let mut funcs = BTreeMap::new();
    let mut heads = BTreeSet::new();
    let mut i = 0;
    while tokens.get(i).is_some_and(|t| t.tok != Tok::Eof) {
        let pos = tokens[i].pos;
        let public = word(tokens.get(i), "pub");
        if public { i += 1; }
        if !word(tokens.get(i), "fnc") {
            return Err(diagnostic(pos, "E_UNSUPPORTED", "imported files support only direct braced fnc declarations"));
        }
        i += 1;
        let head = i;
        let name = take_ident(tokens, &mut i, pos)?;
        if tokens.get(i).is_none_or(|t| t.tok != Tok::LParen) {
            return Err(diagnostic(pos, "E_UNSUPPORTED", "imported function must have parameters"));
        }
        if funcs.insert(name.clone(), public).is_some() {
            return Err(diagnostic(pos, "E_DUPLICATE", format!("duplicate imported function '{name}'")));
        }
        heads.insert(head);
        while tokens.get(i).is_some_and(|t| !matches!(t.tok, Tok::LBrace | Tok::Eof)) { i += 1; }
        if tokens.get(i).is_none_or(|t| t.tok != Tok::LBrace) {
            return Err(diagnostic(pos, "E_UNSUPPORTED", "imported function must have a braced body"));
        }
        i += 1;
        let mut depth = 1;
        while depth > 0 {
            match tokens.get(i).map(|t| &t.tok) {
                Some(Tok::LBrace) => depth += 1,
                Some(Tok::RBrace) => depth -= 1,
                Some(Tok::Eof) | None => return Err(diagnostic(pos, "E_EOF", "unterminated imported function")),
                _ => {}
            }
            if depth > 128 { return Err(diagnostic(pos, "E_NESTING", "imported function nested too deep")); }
            i += 1;
        }
        if funcs.len() > MAX_BINDINGS {
            return Err(diagnostic(pos, "E_SOURCE_LIMIT", "too many imported functions"));
        }
    }
    let mut parser = Parser::new(tokens.to_vec());
    let program = parser.parse_program()?;
    if program.funcs.len() != funcs.len() || !program.classes.is_empty()
        || !program.actors.is_empty() || !program.enums.is_empty() {
        return Err(diagnostic(tokens[0].pos, "E_UNSUPPORTED", "imported source is not a direct-function module"));
    }
    Ok((funcs, heads))
}
fn reject_forged_names(tokens: &[Token]) -> R<()> {
    for t in tokens {
        if let Tok::Ident(s) = &t.tok {
            if s.starts_with(RESERVED) {
                return Err(diagnostic(t.pos, "E_RESERVED", "reserved compiler-generated import namespace"));
            }
        }
    }
    Ok(())
}

/// Resolves one hop of relative .atto function imports and returns real
/// tokens for the normal module-lower, production parser, checker and emitter.
pub fn lower<F>(source: &str, logical: &Path, mut load: F) -> R<Vec<Token>>
where F: FnMut(&Path) -> std::io::Result<String> {
    if source.len() > crate::SOURCE_CAP {
        return Err(diagnostic(Pos { line: 1, col: 1 }, "E_SOURCE_LIMIT", "source exceeds 64 KiB"));
    }
    if logical.is_absolute() {
        return Err(diagnostic(Pos { line: 1, col: 1 }, "E_IMPORT_PATH", "logical path must be project-relative"));
    }
    let lexed = lexer::lex(source)?;
    let (imports, root) = imports(&lexed, logical)?;
    if imports.is_empty() { return Ok(lexed); }
    reject_forged_names(&root)?;
    let mut parser = Parser::new(root.clone());
    let program = parser.parse_program()?;
    if !program.classes.is_empty() || !program.actors.is_empty() || !program.enums.is_empty() {
        return Err(diagnostic(imports[0].pos, "E_UNSUPPORTED", "file linking currently supports direct functions only"));
    }
    let root_names: BTreeSet<_> = program.funcs.iter().map(|f| f.name.clone()).collect();
    let mut units = Vec::<FileUnit>::new();
    let mut by_path = BTreeMap::<PathBuf, usize>::new();
    let mut bound = BTreeMap::<String, Binding>::new();

    for imp in &imports {
        let id = if let Some(id) = by_path.get(&imp.path) { *id } else {
            if units.len() >= MAX_FILES {
                return Err(diagnostic(imp.pos, "E_SOURCE_LIMIT", "too many imported files"));
            }
            let text = load(&imp.path).map_err(|e| diagnostic(imp.pos, "E_IMPORT",
                format!("cannot load source {}: {:?}", imp.path.display(), e.kind())))?;
            if text.len() > crate::SOURCE_CAP {
                return Err(diagnostic(imp.pos, "E_SOURCE_LIMIT", "imported file exceeds 64 KiB"));
            }
            let toks = lexer::lex(&text)?;
            reject_forged_names(&toks)?;
            let (funcs, heads) = functions(&toks)?;
            let module = format!("{RESERVED}{}", units.len());
            let mut rewritten = Vec::new();
            for (i, t) in toks.iter().enumerate() {
                if !heads.contains(&i) && toks.get(i + 1).is_some_and(|t| t.tok == Tok::LParen)
                    && (i == 0 || toks[i - 1].tok != Tok::Dot) {
                    if let Tok::Ident(name) = &t.tok {
                        if funcs.contains_key(name) {
                            rewritten.push(ident(&module, t.pos));
                            rewritten.push(token(Tok::Dot, t.pos));
                        }
                    }
                }
                if t.tok != Tok::Eof { rewritten.push(t.clone()); }
            }
            let id = units.len();
            by_path.insert(imp.path.clone(), id);
            units.push(FileUnit { module, funcs, tokens: rewritten });
            id
        };
        let file = &units[id];
        let mut add = |local: String, export: Option<String>| -> R<()> {
            if root_names.contains(&local) || bound.contains_key(&local) {
                return Err(diagnostic(imp.pos, "E_DUPLICATE", format!("import '{local}' collides with local declaration")));
            }
            bound.insert(local, Binding { module: file.module.clone(), export });
            Ok(())
        };
        if let Some(alias) = &imp.namespace { add(alias.clone(), None)?; }
        for (export, local) in &imp.names {
            match file.funcs.get(export) {
                None => return Err(diagnostic(imp.pos, "E_UNBOUND", format!("unknown exported function '{export}'"))),
                Some(false) => return Err(diagnostic(imp.pos, "E_PRIVATE", format!("private imported function '{export}'"))),
                Some(true) => add(local.clone(), Some(export.clone()))?,
            }
        }
    }
    let mut rewritten_root = Vec::new();
    for (i, t) in root.iter().enumerate() {
        if let Tok::Ident(name) = &t.tok {
            if let Some(binding) = bound.get(name) {
                if let Some(export) = &binding.export {
                    if root.get(i + 1).is_none_or(|next| next.tok != Tok::LParen)
                        || (i > 0 && root[i - 1].tok == Tok::Dot) {
                        return Err(diagnostic(t.pos, "E_UNSUPPORTED", "imported function supports direct calls only"));
                    }
                    rewritten_root.push(ident(&binding.module, t.pos));
                    rewritten_root.push(token(Tok::Dot, t.pos));
                    rewritten_root.push(ident(export, t.pos));
                } else {
                    let member = match (root.get(i + 1), root.get(i + 2), root.get(i + 3)) {
                        (Some(a), Some(Token { tok: Tok::Ident(s), .. }), Some(c))
                            if a.tok == Tok::Dot && c.tok == Tok::LParen => s,
                        _ => return Err(diagnostic(t.pos, "E_UNSUPPORTED", "namespace import supports qualified direct calls only")),
                    };
                    let file = units.iter().find(|f| f.module == binding.module)
                        .expect("internal import binding references existing file");
                    match file.funcs.get(member) {
                        None => return Err(diagnostic(t.pos, "E_UNBOUND", format!("unknown namespace function '{}.{member}'", name))),
                        Some(false) => return Err(diagnostic(t.pos, "E_PRIVATE", format!("private namespace function '{}.{member}'", name))),
                        Some(true) => {}
                    }
                    rewritten_root.push(ident(&binding.module, t.pos));
                }
                continue;
            }
        }
        if t.tok != Tok::Eof { rewritten_root.push(t.clone()); }
    }

    let mut all = Vec::new();
    for file in units {
        let pos = imports[0].pos;
        all.extend([ident("define", pos), ident("module", pos), ident(&file.module, pos), ident("as", pos)]);
        all.extend(file.tokens);
        all.push(ident("end", pos));
    }
    all.extend(rewritten_root);
    all.push(token(Tok::Eof, root.last().map_or(Pos::default(), |t| t.pos)));
    if all.len() > MAX_TOKENS {
        return Err(diagnostic(imports[0].pos, "E_SOURCE_LIMIT", "linked source token budget exceeded"));
    }
    Ok(all)
}
