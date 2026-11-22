//! Bounded, in-memory multi-hop namespace/module linking for the browser guest.
//!
//! Deterministic static source linker. The host-side package resolver is separate.
//! The caller must provide source units and trusted canonical package identities;
//! no symlinks, network fetches or host capabilities are consulted. Reject
//! import cycles, conflicting module names and untrusted re-exports before Wasm.
use crate::diag::{Diagnostic, Pos};
use crate::lexer::{self, Tok, Token};
use crate::{module_lower, parser, visibility_lower};
use std::collections::BTreeMap;

type R<T> = Result<T, Diagnostic>;
const MAX_UNITS: usize = 64;
const MAX_IMPORTS: usize = 32;

#[derive(Clone, Copy, Debug)]
pub struct LinkUnit<'a> {
    /// Normalized, project-relative POSIX path such as "src/main.atto".
    pub path: &'a str,
    /// Canonical package identity supplied by an already trusted resolver.
    pub package: &'a str,
    pub source: &'a str,
}

#[derive(Clone, Debug)]
struct Import {
    name: String,
    path: String,
    pos: Pos,
}

struct Header {
    namespace: Option<String>,
    imports: Vec<Import>,
    body: String,
}

fn err(pos: Pos, code: &'static str, message: impl Into<String>) -> Diagnostic {
    Diagnostic::new(pos, code, message)
}

fn word(t: &[Token], i: usize, val: &str) -> bool {
    matches!(t.get(i).map(|x| &x.tok), Some(Tok::Ident(s)) if s == val)
}
fn ident(t: &[Token], i: usize) -> R<String> {
    match t.get(i) {
        Some(Token {
            tok: Tok::Ident(s),
            pos: _,
        }) if s.len() <= 64
            && !s.is_empty()
            && s.is_ascii()
            && (s.as_bytes()[0].is_ascii_alphabetic() || s.starts_with('_'))
            && s.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_') =>
        {
            Ok(s.clone())
        }
        Some(t) => Err(err(t.pos, "E_SYNTAX", "expected bounded ASCII namespace/module identifier")),
        None => Err(err(Pos::default(), "E_EOF", "expected identifier")),
    }
}

/// Token positions are Unicode scalar columns, not bytes. Calculate the
/// original byte offset explicitly; never byte-slice using a column directly.
fn byte_at(source: &str, pos: Pos) -> R<usize> {
    let (mut line, mut col) = (1u32, 1u32);
    for (offset, c) in source.char_indices() {
        if line == pos.line && col == pos.col {
            return Ok(offset);
        }
        if c == '\n' {
            line += 1;
            col = 1;
        } else {
            col += 1;
        }
    }
    if line == pos.line && col == pos.col {
        return Ok(source.len());
    }
    Err(err(pos, "E_SYNTAX", "invalid source token boundary"))
}
fn erase(mut source: String, mut spans: Vec<(usize, usize)>) -> String {
    spans.sort_unstable_by(|a, b| b.0.cmp(&a.0));
    for (start, end) in spans {
        source.replace_range(start..end, "");
    }
    source
}
fn span(source: &str, toks: &[Token], start: usize, semi: usize) -> R<(usize, usize)> {
    if toks.get(semi).map(|t| &t.tok) != Some(&Tok::Semi) {
        return Err(err(toks[start].pos, "E_SYNTAX", "missing semicolon"));
    }
    Ok((byte_at(source, toks[start].pos)?, byte_at(source, toks[semi].pos)? + 1))
}

pub(crate) fn path_ok(path: &str) -> bool {
    path.len() <= 256
        && !path.is_empty()
        && path.is_ascii()
        && path.ends_with(".atto")
        && !path.starts_with('/')
        && !path.contains('\\')
        && !path.contains(':')
        && path.split('/').all(|part| {
            !part.is_empty()
                && part != "."
                && part != ".."
                && part.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-' | b'.'))
        })
}
fn package_ok(package: &str) -> bool {
    !package.is_empty()
        && package.len() <= 128
        && package.is_ascii()
        && package
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-' | b'.' | b'@'))
}
fn relative_path(from: &str, rel: &str, pos: Pos) -> R<String> {
    let (prefix, suffix) = if let Some(rest) = rel.strip_prefix("./") {
        ("relative", rest)
    } else if let Some(rest) = rel.strip_prefix('+') {
        ("root", rest)
    } else {
        return Err(err(
            pos,
            "E_IMPORT_PATH",
            "imports require './name.atto' or '+name.atto' canonical paths",
        ));
    };
    if !path_ok(suffix) {
        return Err(err(pos, "E_IMPORT_PATH", "import path has noncanonical components or traversal"));
    }
    let dir = from.rsplit_once('/').map(|(dir, _)| dir).unwrap_or("");
    let resolved = if prefix == "root" || dir.is_empty() {
        suffix.to_string()
    } else {
        format!("{dir}/{suffix}")
    };
    if !path_ok(&resolved) {
        return Err(err(pos, "E_IMPORT_PATH", "invalid canonical import destination"));
    }
    Ok(resolved)
}

/// Consume only a first-position namespace header and following import module
/// declarations. No import is executable merely because its name occurs in a
/// namespace. The path must resolve exactly among provided input units.
fn header(source: &str, file: &str) -> R<Header> {
    // Normalize explicit/dotted and folder-derived namespace spellings using
    // the same production grammar as the single-file compiler.
    let raw = lexer::lex(source)?;
    let t = crate::namespace::normalize(&raw, Some(std::path::Path::new(file)))?;
    let mut i = 0;
    let mut namespace = None;
    let mut imports = Vec::new();
    let mut spans = Vec::new();
    if word(&t, i, "use") {
        if !word(&t, i + 1, "namespace") {
            return Err(err(t[i].pos, "E_SYNTAX", "expected namespace header before imports"));
        }
        let mut at = i + 2;
        let mut parts = vec![ident(&t, at)?];
        at += 1;
        while t.get(at).map(|x| &x.tok) == Some(&Tok::Dot) {
            parts.push(ident(&t, at + 1)?);
            at += 2;
            if parts.len() > 32 {
                return Err(err(t[i].pos, "E_NAMESPACE_DEPTH", "namespace has more than 32 components"));
            }
        }
        if t.get(at).map(|x| &x.tok) != Some(&Tok::Semi) {
            return Err(err(t[i].pos, "E_SYNTAX", "namespace must end with a semicolon"));
        }
        namespace = Some(parts.join("."));
        // For a default namespace, the normalized token sequence is longer
        // or shorter than the original header. Erase the span using *raw*
        // token offsets, not synthetic token positions.
        let raw_semi = raw.iter().position(|token| token.tok == Tok::Semi)
            .ok_or_else(|| err(t[i].pos, "E_SYNTAX", "missing namespace semicolon"))?;
        spans.push(span(source, &raw, 0, raw_semi)?);
        i = at + 1;
    }
    while word(&t, i, "import") {
        let pos = t[i].pos;
        if !word(&t, i + 1, "module") || !word(&t, i + 3, "from") {
            return Err(err(pos, "E_SYNTAX", "expected 'import module Name from \"./file.atto\";'"));
        }
        let name = ident(&t, i + 2)?;
        let path = match t.get(i + 4).map(|x| &x.tok) {
            Some(Tok::Str(s)) => relative_path(file, s, pos)?,
            _ => return Err(err(pos, "E_SYNTAX", "module import requires quoted relative path")),
        };
        if t.get(i + 5).map(|x| &x.tok) != Some(&Tok::Semi) {
            return Err(err(pos, "E_SYNTAX", "module import must end with a semicolon"));
        }
        if imports.len() >= MAX_IMPORTS {
            return Err(err(pos, "E_SOURCE_LIMIT", "too many module imports"));
        }
        if imports.iter().any(|x: &Import| x.name == name || x.path == path) {
            return Err(err(pos, "E_DUPLICATE", "duplicate imported module alias or path"));
        }
        imports.push(Import { name, path, pos });
        spans.push(span(source, &t, i, i + 5)?);
        i += 6;
    }
    // Both forms are declarations, not expressions. A second header or
    // import (including an attempted nested one) must never be silently lost.
    for token in &t[i..] {
        if let Tok::Ident(s) = &token.tok {
            if s == "import" || s == "namespace" {
                return Err(err(
                    token.pos,
                    "E_SYNTAX",
                    "namespace headers and module imports must precede all declarations",
                ));
            }
        }
    }
    Ok(Header {
        namespace,
        imports,
        body: erase(source.to_string(), spans),
    })
}

fn imported_module(imp: &Import, dependency: &LinkUnit<'_>, entry_package: &str) -> R<String> {
    let hdr = header(dependency.source, dependency.path)?;
    // Each import edge was validated against the whole graph before this
    // function is called. Module imports in the header are not declarations
    // and are erased before local parsing; no transitive runtime authority.
    let tokens = lexer::lex(&hdr.body)?;
    // Only a real, file-owned module declaration may satisfy an import.
    let mut visible = visibility_lower::lower(tokens.clone())?;
    let lowered = module_lower::lower_with_manifest(visible.tokens)?;
    if lowered.modules.len() != 1 || lowered.modules[0].name != imp.name {
        return Err(err(
            imp.pos,
            "E_UNBOUND",
            format!("import '{}' does not match exactly one declared module in its source", imp.name),
        ));
    }
    let parsed = parser::Parser::new(lowered.tokens).parse_program()?;
    if !parsed.classes.is_empty()
        || !parsed.actors.is_empty()
        || !parsed.enums.is_empty()
        || parsed.funcs.iter().any(|f| !f.name.starts_with("__atto_internal_module_"))
    {
        return Err(err(
            imp.pos,
            "E_UNSUPPORTED",
            "imported source must contain only the requested module and its functions",
        ));
    }
    if visible.exports.iter().any(|e| e.name != imp.name) {
        return Err(err(
            imp.pos,
            "E_UNSUPPORTED",
            "imported module source cannot expose unrelated file-level symbols",
        ));
    }
    visibility_lower::resolve_exports_with_modules(&mut visible.exports, &parsed, &lowered.modules)?;
    let module = &lowered.modules[0];
    let allowed = visible.exports.iter().find(|e| e.name == imp.name);
    let exported = allowed.is_some() || module.inline_public;
    if !exported {
        return Err(err(imp.pos, "E_VISIBILITY", "imported module is not exported"));
    }
    let public = module.inline_public || allowed.is_some_and(|e| e.visibility == visibility_lower::Visibility::Public);
    if !public && dependency.package != entry_package {
        return Err(err(
            imp.pos,
            "E_VISIBILITY",
            "pub[pkg] module cannot be imported by a different package",
        ));
    }
    // Erase all file export directives: a referenced module's visibility is
    // checked above, but must NOT be serialized as an entry-file re-export.
    let mut spans = Vec::new();
    let mut i = 0;
    while i < tokens.len() {
        if word(&tokens, i, "export") {
            let (_, next) = visibility_lower::parse_export_directive(&tokens, i)?;
            spans.push(span(&hdr.body, &tokens, i, next - 1)?);
            i = next;
            continue;
        }
        if word(&tokens, i, "internal") || word(&tokens, i, "external") || word(&tokens, i, "protected") {
            return Err(err(
                tokens[i].pos,
                "E_UNSUPPORTED",
                "imported module cannot contain unresolved local/foreign/protected modifiers",
            ));
        }
        // An imported inline-public module is made file-local in the
        // combined guest, so it cannot become an accidental re-export.
        if word(&tokens, i, "define") && word(&tokens, i + 1, "pub") && word(&tokens, i + 2, "module") {
            let start = byte_at(&hdr.body, tokens[i + 1].pos)?;
            spans.push((start, start + 3)); // exact ASCII 'pub'
            i += 3;
            continue;
        }
        i += 1;
    }
    Ok(erase(hdr.body, spans))
}

/// Read only the declared import graph; the filesystem resolver uses this
/// API to resolve physical source paths without scanning unrelated files.
pub(crate) fn import_paths(source: &str, file: &str) -> R<Vec<String>> {
    Ok(header(source, file)?.imports.into_iter().map(|i| i.path).collect())
}

/// Detect cycles and resolve all reachable modules before a single guest is
/// emitted. Postorder ensures that imported declarations precede their users.
/// Every edge checks visibility against its immediate *consumer's* package,
/// never against a globally trusted entry-package string.
fn visit<'a>(
    path: &'a str,
    files: &BTreeMap<&'a str, &'a LinkUnit<'a>>,
    states: &mut BTreeMap<&'a str, u8>,
    names: &mut BTreeMap<String, &'a str>,
    ordered: &mut Vec<(String, String)>,
    edges: &mut usize,
    entry: &'a str,
) -> R<()> {
    match states.get(path).copied() {
        Some(1) => return Err(err(Pos::default(), "E_IMPORT_CYCLE", format!("cyclic module import at '{path}'"))),
        Some(2) => return Ok(()),
        _ => {}
    }
    let unit = files
        .get(path)
        .ok_or_else(|| err(Pos::default(), "E_UNBOUND", format!("missing source file '{path}'")))?;
    states.insert(path, 1);
    let hdr = header(unit.source, path)?;
    // A file may import different file-local modules, but this executable
    // profile permits exactly one module declaration per imported source.
    if path != entry {
        let source = lexer::lex(&hdr.body)?;
        let vis = visibility_lower::lower(source)?;
        let module = module_lower::lower_with_manifest(vis.tokens)?;
        if module.modules.len() != 1 {
            return Err(err(
                Pos::default(),
                "E_UNSUPPORTED",
                "imported files must own exactly one named module",
            ));
        }
        let name = &module.modules[0].name;
        if let Some(previous) = names.insert(name.clone(), path) {
            if previous != path {
                return Err(err(
                    Pos::default(),
                    "E_DUPLICATE",
                    format!("ambiguous module name '{name}' in '{previous}' and '{path}'"),
                ));
            }
        }
    }
    for imp in &hdr.imports {
        *edges += 1;
        if *edges > MAX_UNITS * MAX_IMPORTS {
            return Err(err(imp.pos, "E_SOURCE_LIMIT", "module import graph edge budget exceeded"));
        }
        if imp.path == path {
            return Err(err(imp.pos, "E_IMPORT_CYCLE", "module cannot import itself"));
        }
        let dep = files
            .get(imp.path.as_str())
            .ok_or_else(|| err(imp.pos, "E_UNBOUND", format!("missing imported source '{}'", imp.path)))?;
        // Module visibility and real ownership must be checked on every
        // edge, even when another parent already traversed this dependency.
        let _ = imported_module(imp, dep, unit.package)?;
        visit(dep.path, files, states, names, ordered, edges, entry)?;
    }
    states.insert(path, 2);
    if path != entry {
        // All imports were visited first. Pick the owning module name from
        // its only checked declaration; foreign exports cannot forge it.
        let tokens = lexer::lex(&hdr.body)?;
        let lowered = visibility_lower::lower(tokens)?;
        let manifest = module_lower::lower_with_manifest(lowered.tokens)?;
        let m = manifest
            .modules
            .first()
            .ok_or_else(|| err(Pos::default(), "E_UNBOUND", "module missing after graph traversal"))?;
        let synthetic = Import {
            name: m.name.clone(),
            path: path.to_string(),
            pos: m.pos,
        };
        ordered.push((path.to_string(), imported_module(&synthetic, unit, unit.package)?));
    }
    Ok(())
}

/// Validate file-local import authority before flattening source scopes.
/// Concatenation MUST NOT turn an import in A into ambient authority in B.
/// In this module profile, each file can use only its own named module
/// or modules it explicitly imports, even if another file imports the name.
fn preflight_file_authority(
    path: &str,
    source: &str,
    names: &BTreeMap<String, &str>,
    root_funcs: &BTreeMap<String, ()>,
    is_entry: bool,
) -> R<()> {
    let hdr = header(source, path)?;
    let tokens = lexer::lex(&hdr.body)?;
    let lexical = visibility_lower::lower(tokens.clone())?;
    let modules = module_lower::lower_with_manifest(lexical.tokens)?;
    let imported: std::collections::BTreeSet<&str> = hdr.imports.iter().map(|i| i.name.as_str()).collect();
    let owned: std::collections::BTreeSet<&str> = modules.modules.iter().map(|m| m.name.as_str()).collect();
    for (i, token) in tokens.iter().enumerate() {
        let Tok::Ident(ref name) = token.tok else { continue };
        if name.starts_with("__atto_q_") {
            return Err(err(
                token.pos,
                "E_RESERVED",
                "source must not forge a compiler-owned module identity",
            ));
        }
        // A direct call to a root-level function cannot become visible to an
        // imported module just because the final guest has one symbol table.
        if !is_entry
            && root_funcs.contains_key(name)
            && tokens.get(i + 1).map(|t| &t.tok) == Some(&Tok::LParen)
            && (i == 0 || tokens[i - 1].tok != Tok::Dot)
            && (i == 0 || !word(&tokens, i - 1, "fnc"))
        {
            return Err(err(
                token.pos,
                "E_VISIBILITY",
                format!("imported source cannot capture entry-local function '{name}'"),
            ));
        }
        // All module-qualified callsites must be owned or explicitly
        // imported in this file, not in another parent of the graph.
        if names.contains_key(name)
            && tokens.get(i + 1).map(|t| &t.tok) == Some(&Tok::Dot)
            && matches!(tokens.get(i + 2).map(|t| &t.tok), Some(Tok::Ident(_)))
            && tokens.get(i + 3).map(|t| &t.tok) == Some(&Tok::LParen)
            && !owned.contains(name.as_str())
            && !imported.contains(name.as_str())
        {
            return Err(err(
                token.pos,
                "E_VISIBILITY",
                format!("module '{name}' is not explicitly imported in '{path}'"),
            ));
        }
    }
    Ok(())
}

/// Rewrite only checked module declarations and direct qualified callsites
/// to a source-owned internal ID. Source files are scanned independently;
/// strings and comments never become symbol sites, and all user-authored
/// reserved prefixes were rejected before this rewrite.
fn qualify_bound_modules(source: &str, aliases: &BTreeMap<String, String>) -> R<String> {
    let tokens = lexer::lex(source)?;
    let mut edits: Vec<(usize, usize, &str)> = Vec::new();
    for i in 0..tokens.len() {
        let Tok::Ident(ref name) = tokens[i].tok else { continue };
        let Some(alias) = aliases.get(name) else { continue };
        let declaration = (i >= 2 && word(&tokens, i - 2, "define") && word(&tokens, i - 1, "module"))
            || (i >= 3 && word(&tokens, i - 3, "define") && word(&tokens, i - 2, "pub") && word(&tokens, i - 1, "module"));
        let qualified_call = tokens.get(i + 1).map(|t| &t.tok) == Some(&Tok::Dot)
            && matches!(tokens.get(i + 2).map(|t| &t.tok), Some(Tok::Ident(_)))
            && tokens.get(i + 3).map(|t| &t.tok) == Some(&Tok::LParen);
        if declaration || qualified_call {
            let start = byte_at(source, tokens[i].pos)?;
            if edits.len() >= 2048 {
                return Err(err(
                    tokens[i].pos,
                    "E_SOURCE_LIMIT",
                    "too many source-owned module symbol references",
                ));
            }
            edits.push((start, start + name.len(), alias.as_str()));
        }
    }
    let mut rewritten = source.to_owned();
    for (start, end, alias) in edits.into_iter().rev() {
        rewritten.replace_range(start..end, alias);
    }
    Ok(rewritten)
}

/// Compile a bounded, explicit source graph into one linked guest Wasm module.
/// The package identity of each LinkUnit must be authenticated by its trusted
/// caller; this function does not read the filesystem or package manifests.
/// Root '+' and local './' imports both resolve within the supplied file map.
/// Cyclic edges, duplicate module identities and re-exports fail closed.
pub fn compile_linked(entry_path: &str, units: &[LinkUnit<'_>]) -> R<Vec<u8>> {
    if units.is_empty() || units.len() > MAX_UNITS {
        return Err(err(Pos::default(), "E_SOURCE_LIMIT", "invalid linked source-unit count"));
    }
    if !path_ok(entry_path) {
        return Err(err(Pos::default(), "E_IMPORT_PATH", "entry source path is not canonical"));
    }
    let mut files = BTreeMap::new();
    for unit in units {
        if !path_ok(unit.path) || !package_ok(unit.package) {
            return Err(err(
                Pos::default(),
                "E_IMPORT_PATH",
                "source path or package identity is not canonical",
            ));
        }
        // Reject untrusted oversized inputs before any lexing or graph
        // traversal. The combined browser guest cannot exceed this cap.
        if unit.source.len() > crate::SOURCE_CAP {
            return Err(err(
                Pos { line: 1, col: 1 },
                "E_SOURCE_LIMIT",
                "source unit exceeds browser source limit",
            ));
        }
        if files.insert(unit.path, unit).is_some() {
            return Err(err(Pos::default(), "E_DUPLICATE", "duplicate canonical source file path"));
        }
    }
    let root = files.get(entry_path).ok_or_else(|| {
        err(
            Pos::default(),
            "E_UNBOUND",
            "entry file does not exist in the supplied source graph",
        )
    })?;
    let hdr = header(root.source, entry_path)?;
    let mut ordered = Vec::new();
    let mut states = BTreeMap::new();
    let mut names = BTreeMap::new();
    let mut edges = 0;
    visit(entry_path, &files, &mut states, &mut names, &mut ordered, &mut edges, entry_path)?;
    // The caller must supply precisely the reachable source graph. Never
    // silently ignore a source unit that was included in a verified snapshot:
    // it may hide stale, malicious, or unauthorized package content.
    for path in files.keys() {
        if states.get(*path).copied() != Some(2) {
            return Err(err(
                Pos { line: 1, col: 1 },
                "E_UNBOUND",
                format!("unreachable source unit '{path}' in linked package graph"),
            ));
        }
    }
    // Imported module names cannot be locally defined *or* exported in the
    // entry file. Otherwise the combined frontend could incorrectly grant
    // the entry's own export visibility to another package's symbol.
    let own = visibility_lower::lower(lexer::lex(&hdr.body)?)?;
    for export in &own.exports {
        if names.contains_key(&export.name) {
            return Err(err(
                export.pos,
                "E_VISIBILITY",
                "imported module is not re-exportable in this linker profile",
            ));
        }
    }
    let local = module_lower::lower_with_manifest(own.tokens)?;
    for module in &local.modules {
        if names.contains_key(&module.name) {
            return Err(err(module.pos, "E_DUPLICATE", "entry file module conflicts with imported module"));
        }
    }
    // Identify entry-owned top-level functions before concatenation, then
    // reject cross-file free-function capture and ambient module authority.
    let root_parsed = parser::Parser::new(local.tokens).parse_program()?;
    let mut root_funcs = BTreeMap::new();
    for function in root_parsed.funcs {
        if !function.name.starts_with("__atto_internal_module_") {
            root_funcs.insert(function.name, ());
        }
    }
    for (path, unit) in &files {
        if states.get(*path) == Some(&2) {
            preflight_file_authority(path, unit.source, &names, &root_funcs, *path == entry_path)?;
        }
    }
    // Translate source-owned qualified module identities to deterministic
    // backend-safe aliases before lowering. This is an intermediate bridge;
    // future backends should directly consume typed linked IR instead.
    let mut aliases = BTreeMap::<String, String>::new();
    let mut alias_owners = BTreeMap::<String, String>::new();
    for (name, path) in &names {
        let dep = files
            .get(*path)
            .ok_or_else(|| err(Pos::default(), "E_UNBOUND", "module owner vanished during linking"))?;
        let owner_header = header(dep.source, path)?;
        let namespace = owner_header.namespace.unwrap_or_else(|| "_file".into());
        let key = crate::linked_symbols::QualifiedModuleId::new(dep.package, &namespace, path, name)?;
        let alias = key.internal_name();
        if let Some(previous) = alias_owners.insert(alias.clone(), path.to_string()) {
            if previous != *path {
                return Err(err(
                    Pos::default(),
                    "E_SYMBOL_ID",
                    "two distinct modules collide after qualified symbol mangling",
                ));
            }
        }
        aliases.insert(name.clone(), alias);
    }
    let mut pieces: Vec<String> = Vec::with_capacity(ordered.len() + 1);
    for (_, data) in ordered {
        pieces.push(qualify_bound_modules(&data, &aliases)?);
    }
    pieces.push(qualify_bound_modules(&hdr.body, &aliases)?);
    let linked = pieces.join("\n");
    if linked.len() > crate::SOURCE_CAP {
        return Err(err(
            Pos::default(),
            "E_SOURCE_LIMIT",
            "linked guest source exceeds browser source limit",
        ));
    }
    let _entry_namespace = hdr.namespace;
    crate::compile(&linked)
}
