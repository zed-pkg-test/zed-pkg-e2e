//! A deliberately bounded Oreslang parser for the shared v1 scalar conformance surface.
//! No implicit support for actors, async, heap ownership, or polymorphic calls.
use logos::Logos;
use std::fmt;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Program {
    pub functions: Vec<Function>,
    /// Explicit file-level export metadata, including package-only names.
    pub exports: Vec<ExportDecl>,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ExportScope { Public, Package }
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ExportDecl {
    pub name: String,
    pub scope: ExportScope,
    pub kind: Option<String>,
    pub offset: usize,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Function {
    pub name: String,
    pub exported: bool,
    pub params: Vec<String>,
    pub locals: Vec<Local>,
    pub expression: Expr,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Local {
    pub name: String,
    pub value: Expr,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Expr {
    Integer(i64),
    Name(String),
    Call { name: String, args: Vec<Expr> },
    Add(Box<Expr>, Box<Expr>),
    Subtract(Box<Expr>, Box<Expr>),
    Multiply(Box<Expr>, Box<Expr>),
    Negate(Box<Expr>),
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ParseError {
    pub offset: usize,
    pub class: &'static str,
    pub message: String,
}
impl fmt::Display for ParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "{} at byte {}: {}",
            self.class, self.offset, self.message
        )
    }
}
impl std::error::Error for ParseError {}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Logos)]
enum Kind {
    #[token("pub")]
    Pub,
    #[token("export")]
    Export,
    #[token("class")]
    Class,
    #[token("define")]
    Define,
    #[token("static")]
    Static,
    #[token("fnc")]
    Fnc,
    #[token("return")]
    Return,
    #[token("const")]
    Const,
    #[token("val")]
    Val,
    #[token("int")]
    Int,
    #[token("as")]
    As,
    #[token("end")]
    End,
    #[token("->")]
    Arrow,
    #[token("(")]
    LParen,
    #[token(")")]
    RParen,
    #[token("{")]
    LBrace,
    #[token("[")]
    LBracket,
    #[token("]")]
    RBracket,
    #[token("}")]
    RBrace,
    #[token(";")]
    Semicolon,
    #[token(":")]
    Colon,
    #[token("=")]
    Equals,
    #[token(",")]
    Comma,
    #[token("+")]
    Plus,
    #[token("-")]
    Minus,
    #[token("*")]
    Star,
    // Explicitly classify removed operators instead of letting a generic
    // lexer error obscure the canonical Java/LLVM diagnostic family.
    #[token("%")]
    RemovedModulo,
    #[token("^^")]
    RemovedDoubleCaret,
    #[token("^")]
    RemovedCaret,
    #[regex(r"[0-9][0-9_]*")]
    Number,
    #[regex(r"[A-Za-z_][A-Za-z0-9_]*")]
    Identifier,
    #[regex(r"[ \t\n\r\f\u{0085}\u{2028}\u{2029}]+|//[^\r\n\u{0085}\u{2028}\u{2029}]*", logos::skip, allow_greedy = true)]
    Whitespace,
}
#[derive(Clone, Debug)]
struct Token {
    kind: Kind,
    text: String,
    start: usize,
}
// Keep source comments aligned with the Logos lexer, which recognizes
// CR, LF, NEL (U+0085), LINE SEPARATOR and PARAGRAPH SEPARATOR. Offsets stay
// byte-based so a // comment cannot hide a later /* block or actor declaration.
fn line_break_width(bytes: &[u8], at: usize) -> usize {
    match bytes.get(at).copied() {
        Some(b'\r' | b'\n') => 1,
        Some(0xc2) if bytes.get(at + 1) == Some(&0x85) => 2,
        Some(0xe2)
            if bytes.get(at + 1) == Some(&0x80)
                && matches!(bytes.get(at + 2), Some(0xa8 | 0xa9)) =>
        {
            3
        }
        _ => 0,
    }
}

/// Strip nested block comments without moving token offsets. Unsupported Unicode
/// inside a comment becomes ASCII whitespace; outside stays byte-identical.
fn without_block_comments(source: &str) -> Result<String, ParseError> {
    let mut bytes = source.as_bytes().to_vec();
    let src = source.as_bytes();
    let mut i = 0;
    let mut depth = 0_u32;
    let mut open = 0_usize;
    while i < src.len() {
        // A block opener inside a // line comment is not a block comment.
        // Leave the line untouched for Logos to skip; preserve source offsets.
        if depth == 0 && i + 1 < src.len() && src[i] == b'/' && src[i + 1] == b'/' {
            while i < src.len() && line_break_width(src, i) == 0 {
                i += 1;
            }
        } else if i + 1 < src.len() && src[i] == b'/' && src[i + 1] == b'*' {
            if depth == 0 {
                open = i;
            }
            depth += 1;
            bytes[i] = b' ';
            bytes[i + 1] = b' ';
            i += 2;
        } else if i + 1 < src.len() && src[i] == b'*' && src[i + 1] == b'/' && depth > 0 {
            depth -= 1;
            bytes[i] = b' ';
            bytes[i + 1] = b' ';
            i += 2;
        } else {
            if depth > 0 && src[i] != b'\n' && src[i] != b'\r' {
                bytes[i] = b' ';
            }
            i += 1;
        }
    }
    if depth != 0 {
        return Err(ParseError {
            offset: open,
            class: "unterminated_comment",
            message: "nested /* block comment is not closed".into(),
        });
    }
    String::from_utf8(bytes).map_err(|_| ParseError {
        offset: 0,
        class: "syntax_error",
        message: "comment preprocessing failed".into(),
    })
}

// Only a single underscore between decimal digits is admitted.
// Without this check, tokens such as 1__0 and 1_ silently normalize to 10 and
// 1, allowing malformed source to compile with a different spelling.
fn normalized_integer_digits(token: &Token) -> Result<String, ParseError> {
    let bytes = token.text.as_bytes();
    for index in 0..bytes.len() {
        if bytes[index] == b'_'
            && (index == 0
                || index + 1 == bytes.len()
                || !bytes[index - 1].is_ascii_digit()
                || !bytes[index + 1].is_ascii_digit())
        {
            return Err(ParseError {
                offset: token.start + index,
                class: "syntax_error",
                message: "invalid integer digit separator".into(),
            });
        }
    }
    Ok(token.text.replace('_', ""))
}

pub const MAX_SOURCE_BYTES: usize = 256 * 1024;
pub const MAX_TOKENS: usize = 2048;
pub const MAX_PAREN_NESTING: usize = 64;
pub const MAX_PRIMARY_DEPTH: usize = 64;
pub const MAX_INFIX_OPERATORS: usize = 64;

/// Execution-domain words reserved by every Oreslang backend (Java and LLVM
/// reject them as identifiers too).
pub const EXECUTION_DOMAIN_WORDS: [&str; 3] = ["cpu", "gpu", "quantum"];

/// This compiler lowers no cpu/gpu/quantum region or CPU/GPU Task. Reject them
/// with a dedicated diagnostic before parsing, never an incidental syntax
/// error that a later grammar extension could turn into host execution.
fn reject_execution_domains(tokens: &[Token]) -> Result<(), ParseError> {
    for (index, token) in tokens.iter().enumerate() {
        if token.kind != Kind::Identifier {
            continue;
        }
        let opens_block = tokens.get(index + 1).map(|next| next.kind) == Some(Kind::LBrace);
        let opens_task_scope = token.text == "task"
            && tokens.get(index + 1).map(|next| next.text.as_str()) == Some("scope")
            && tokens.get(index + 2).map(|next| next.kind) == Some(Kind::LBrace);
        // Await is valid in the language only inside a suspends fnc/mtd.
        // This scalar-only compiler cannot typecheck or lower those functions,
        // so reject them *semantically*, before IR can be emitted.
        if token.text == "await" || token.text == "suspends" {
            return Err(ParseError {
                offset: token.start,
                class: "suspension_unsupported",
                message: format!(
                    "'{}' requires checked suspends/await continuation lowering; this backend cannot execute it",
                    token.text
                ),
            });
        }
        let message = if EXECUTION_DOMAIN_WORDS.contains(&token.text.as_str()) {
            format!(
                "execution domain '{}' is reserved and not lowered by this backend; no implicit CPU fallback",
                token.text
            )
        } else if (opens_block && (token.text == "task" || token.text == "nb"))
            || opens_task_scope {
            "execution task requires scheduler-aware Future lowering; no implicit inline execution"
                .into()
        } else {
            continue;
        };
        return Err(ParseError {
            offset: token.start,
            class: "execution_domain_unsupported",
            message,
        });
    }
    Ok(())
}

/// Typed fail-closed admission shared with the .NET/JS source compiler gates.
/// Source HTTP actors require request/mail ownership, state-loan, cancellation
/// and physical shutdown lowering. An optional host HTTP server is not proof
/// of an executable source actor. Only examine actual lexer tokens, not text
/// inside comments; preserve normal user identifiers such as `http`.
fn reject_http_actor_declarations(tokens: &[Token]) -> Result<(), ParseError> {
    for (index, token) in tokens.iter().enumerate() {
        if token.kind != Kind::Define {
            continue;
        }
        let mut cursor = index + 1;
        let mut http = false;
        while let Some(next) = tokens.get(cursor) {
            let modifier = match next.kind {
                Kind::Pub => true,
                Kind::Identifier => matches!(
                    next.text.as_str(),
                    "private"
                        | "shared"
                        | "untrusted"
                        | "serial"
                        | "http"
                        | "tcp"
                        | "websocket"
                        | "ws"
                        | "stream"
                        | "unary"
                        | "abstract"
                ),
                _ => false,
            };
            if !modifier {
                break;
            }
            http |= next.text == "http";
            cursor += 1;
        }
        if http {
            if let Some(next) = tokens.get(cursor) {
                if next.kind == Kind::Identifier
                    && matches!(next.text.as_str(), "actor" | "isoactor")
                {
                    return Err(ParseError {
                        offset: next.start,
                        class: "http_actor_backend_unsupported",
                        message: "HTTP_ACTOR_BACKEND_UNSUPPORTED: Rust scalar compiler cannot lower source HTTP actor request/mail lifetimes, guarded state, or physical shutdown".into(),
                    });
                }
            }
        }
    }
    Ok(())
}

/// This scalar backend has no singleton identity or guarded shared-memory lowering.
/// The word stays contextual for ordinary scalar identifiers.
fn reject_singleton_declarations(tokens: &[Token]) -> Result<(), ParseError> {
    for (i, token) in tokens.iter().enumerate() {
        let after = match token.kind {
            Kind::Define => {
                if tokens.get(i + 1).map(|t| t.kind) == Some(Kind::Pub) {
                    i + 2
                } else {
                    i + 1
                }
            }
            Kind::Pub if tokens.get(i + 1).map(|t| t.kind) == Some(Kind::Define) => i + 2,
            _ => continue,
        };
        if let Some(next) = tokens.get(after) {
            if next.kind == Kind::Identifier && next.text == "singleton" {
                return Err(ParseError {
                    offset: next.start,
                    class: "singleton_unsupported",
                    message: "ORES-SINGLETON-UNSUPPORTED: singleton requires compiler-resolved application-domain identity, once-only immutable publication and owned shared-region RW lowering".into(),
                });
            }
        }
    }
    Ok(())
}

pub fn parse(source: &str) -> Result<Program, ParseError> {
    if source.len() > MAX_SOURCE_BYTES {
        return Err(ParseError {
            offset: MAX_SOURCE_BYTES,
            class: "resource_limit",
            message: "Oreslang source exceeds 256 KiB prototype limit".into(),
        });
    }
    // Reject raw NUL before preprocessing. Otherwise an ignored /* ... */
    // or // comment could erase it and produce a valid executable AST.
    // The source is already a valid UTF-8 &str; report its byte offset.
    if let Some(offset) = source.bytes().position(|byte| byte == 0) {
        return Err(ParseError {
            offset,
            class: "source_admission",
            message: "source contains NUL byte".into(),
        });
    }
    let prepared = without_block_comments(source)?;
    let mut tokens = Vec::new();
    let mut paren_depth = 0_usize;
    for (kind, range) in Kind::lexer(&prepared).spanned() {
        let kind = kind.map_err(|_| ParseError {
            offset: range.start,
            class: "syntax_error",
            message: "unsupported token".into(),
        })?;
        if tokens.len() >= MAX_TOKENS {
            return Err(ParseError {
                offset: range.start,
                class: "resource_limit",
                message: "token count exceeds prototype limit".into(),
            });
        }
        match kind {
            Kind::RemovedModulo | Kind::RemovedCaret | Kind::RemovedDoubleCaret => {
                return Err(ParseError {
                    offset: range.start,
                    class: "removed_operator",
                    message: "operator is reserved; use named arithmetic functions".into(),
                });
            }
            Kind::LParen => {
                paren_depth += 1;
                if paren_depth > MAX_PAREN_NESTING {
                    return Err(ParseError {
                        offset: range.start,
                        class: "resource_limit",
                        message: "parenthesis depth exceeds prototype limit".into(),
                    });
                }
            }
            Kind::RParen => {
                paren_depth = paren_depth.saturating_sub(1);
            }
            _ => {}
        }
        tokens.push(Token {
            kind,
            text: prepared[range.clone()].to_owned(),
            start: range.start,
        });
    }
    reject_singleton_declarations(&tokens)?;
    reject_execution_domains(&tokens)?;
    reject_http_actor_declarations(&tokens)?;
    let mut parser = Parser {
        tokens,
        cursor: 0,
        source_len: source.len(),
        primary_depth: 0,
    };
    let mut functions = Vec::new();
    let mut exports = Vec::new();
    while parser.peek().is_some() {
        if parser.peek() == Some(Kind::Export) {
            exports.push(parser.export_decl()?);
        } else {
            functions.push(parser.function()?);
        }
    }
    // Collect all symbols before resolving exports. A forward export and an
    // identical bottom-of-file export have the same meaning.
    let mut seen = std::collections::HashSet::new();
    for e in &exports {
        if !seen.insert(e.name.clone()) {
            return Err(ParseError { offset: e.offset, class: "duplicate_export",
                message: format!("duplicate export '{}'", e.name) });
        }
        if let Some(kind) = &e.kind {
            if kind != "fnc" {
                return Err(ParseError { offset: e.offset, class: "export_kind_mismatch",
                    message: format!("'{}' is not an admitted scalar fnc", e.name) });
            }
        }
        let Some(target) = functions.iter_mut().find(|f| f.name == e.name) else {
            return Err(ParseError { offset: e.offset, class: "export_unresolved",
                message: format!("unknown export '{}'", e.name) });
        };
        if e.scope == ExportScope::Package && target.exported {
            return Err(ParseError { offset: e.offset, class: "export_visibility_conflict",
                message: format!("'{}' is already public and cannot be package-only", e.name) });
        }
        if e.scope == ExportScope::Public { target.exported = true; }
    }
    if functions.is_empty() {
        return Err(ParseError {
            offset: 0,
            class: "syntax_error",
            message: "expected a function declaration".into(),
        });
    }
    Ok(Program { functions, exports })
}
struct Parser {
    tokens: Vec<Token>,
    cursor: usize,
    source_len: usize,
    primary_depth: usize,
}
impl Parser {
    fn peek(&self) -> Option<Kind> {
        self.tokens.get(self.cursor).map(|t| t.kind)
    }
    fn pos(&self) -> usize {
        self.tokens
            .get(self.cursor)
            .map_or(self.source_len, |t| t.start)
    }
    fn expect(&mut self, kind: Kind) -> Result<Token, ParseError> {
        if self.peek() != Some(kind) {
            return Err(ParseError {
                offset: self.pos(),
                class: "syntax_error",
                message: format!("expected {:?}, got {:?}", kind, self.peek()),
            });
        }
        let token = self.tokens[self.cursor].clone();
        self.cursor += 1;
        Ok(token)
    }
    fn export_decl(&mut self) -> Result<ExportDecl, ParseError> {
        let offset = self.expect(Kind::Export)?.start;
        self.expect(Kind::Pub)?;
        let scope = if self.peek() == Some(Kind::LBracket) {
            self.expect(Kind::LBracket)?;
            let marker = self.expect(Kind::Identifier)?;
            if marker.text != "pkg" {
                return Err(ParseError { offset: marker.start, class: "export_visibility",
                    message: "only pub[pkg] is supported".into() });
            }
            self.expect(Kind::RBracket)?;
            ExportScope::Package
        } else { ExportScope::Public };
        let kind = if self.peek() == Some(Kind::Fnc) { self.expect(Kind::Fnc)?; Some("fnc".into()) }
            else if self.peek() == Some(Kind::Class) { self.expect(Kind::Class)?; Some("class".into()) }
            else { None };
        let name = self.expect(Kind::Identifier)?.text;
        self.expect(Kind::Semicolon)?;
        Ok(ExportDecl { name, scope, kind, offset })
    }
    fn function(&mut self) -> Result<Function, ParseError> {
        // Canonical AttoLang permits both "define pub fnc" and
        // "pub define fnc"; the declaration still must be a scalar fnc.
        let leading_define = self.peek() == Some(Kind::Define);
        if leading_define {
            self.expect(Kind::Define)?;
        }
        let exported = self.peek() == Some(Kind::Pub);
        if exported {
            self.expect(Kind::Pub)?;
        }
        if !leading_define && self.peek() == Some(Kind::Define) {
            self.expect(Kind::Define)?;
        }
        if self.peek() == Some(Kind::Static) {
            self.expect(Kind::Static)?;
        }
        self.expect(Kind::Fnc)?;
        let name = self.expect(Kind::Identifier)?.text;
        self.expect(Kind::LParen)?;
        let mut params = Vec::new();
        if self.peek() != Some(Kind::RParen) {
            loop {
                // Match Java/LLVM: a scalar parameter may be "int x" or
                // "x: int". Neither syntax allows an untyped parameter.
                let name = if self.peek() == Some(Kind::Int) {
                    self.expect(Kind::Int)?;
                    self.expect(Kind::Identifier)?.text
                } else {
                    let name = self.expect(Kind::Identifier)?.text;
                    self.expect(Kind::Colon)?;
                    self.expect(Kind::Int)?;
                    name
                };
                params.push(name);
                if self.peek() == Some(Kind::Comma) {
                    self.expect(Kind::Comma)?;
                } else {
                    break;
                }
            }
        }
        self.expect(Kind::RParen)?;
        let uses_end = match self.peek() {
            Some(Kind::Arrow) | Some(Kind::Colon) => {
                if self.peek() == Some(Kind::Arrow) {
                    self.expect(Kind::Arrow)?;
                } else {
                    self.expect(Kind::Colon)?;
                }
                self.expect(Kind::Int)?;
                self.expect(Kind::LBrace)?;
                false
            }
            Some(Kind::As) => {
                self.expect(Kind::As)?;
                true
            }
            _ => {
                return Err(ParseError {
                    offset: self.pos(),
                    class: "syntax_error",
                    message: "expected ': int {', '-> int {' or 'as'".into(),
                })
            }
        };
        let mut locals = Vec::new();
        // Canonical scalar locals are immutable by default. All admitted
        // spellings lower to the same typed local; this does not admit mut/let
        // rebinding or infer any non-integer type.
        while matches!(self.peek(), Some(Kind::Const | Kind::Val | Kind::Int)) {
            if self.peek() == Some(Kind::Const) {
                self.expect(Kind::Const)?;
            } else if self.peek() == Some(Kind::Val) {
                self.expect(Kind::Val)?;
            }
            let local_name = if self.peek() == Some(Kind::Int) {
                self.expect(Kind::Int)?;
                self.expect(Kind::Identifier)?.text
            } else {
                let name = self.expect(Kind::Identifier)?.text;
                if self.peek() == Some(Kind::Colon) {
                    self.expect(Kind::Colon)?;
                    self.expect(Kind::Int)?;
                }
                name
            };
            self.expect(Kind::Equals)?;
            let value = self.expression()?;
            self.expect(Kind::Semicolon)?;
            locals.push(Local {
                name: local_name,
                value,
            });
        }
        // A source-level immutable binding must never silently turn into a
        // write; the Rust scalar compiler does not yet lower let/mut.
        if let (Some(name), Some(op)) = (
            self.tokens.get(self.cursor),
            self.tokens.get(self.cursor + 1),
        ) {
            let is_bound = name.kind == Kind::Identifier
                && (params.iter().any(|param| param == &name.text)
                    || locals.iter().any(|local| local.name == name.text));
            let compound_rhs = self.tokens.get(self.cursor + 2).map(|token| token.kind);
            let is_assignment = op.kind == Kind::Equals
                || (matches!(op.kind, Kind::Plus | Kind::Minus)
                    && compound_rhs == Some(Kind::Equals));
            if is_bound && is_assignment {
                return Err(ParseError {
                    offset: name.start,
                    class: "immutable_binding",
                    message: "immutable binding cannot be reassigned; let/mut unsupported".into(),
                });
            }
        }
        self.expect(Kind::Return)?;
        let expression = self.expression()?;
        self.expect(Kind::Semicolon)?;
        if uses_end {
            self.expect(Kind::End)?;
        } else {
            self.expect(Kind::RBrace)?;
        }
        Ok(Function {
            name,
            exported,
            params,
            locals,
            expression,
        })
    }
    fn expression(&mut self) -> Result<Expr, ParseError> {
        let mut lhs = self.product()?;
        let mut infix_count = 0_usize;
        loop {
            if matches!(self.peek(), Some(Kind::Plus | Kind::Minus)) {
                if infix_count >= MAX_INFIX_OPERATORS {
                    return Err(ParseError {
                        offset: self.pos(),
                        class: "resource_limit",
                        message: "additive expression exceeds scalar depth budget".into(),
                    });
                }
                infix_count += 1;
            }
            lhs = match self.peek() {
                Some(Kind::Plus) => {
                    self.expect(Kind::Plus)?;
                    Expr::Add(Box::new(lhs), Box::new(self.product()?))
                }
                Some(Kind::Minus) => {
                    self.expect(Kind::Minus)?;
                    Expr::Subtract(Box::new(lhs), Box::new(self.product()?))
                }
                _ => break,
            };
        }
        Ok(lhs)
    }
    fn product(&mut self) -> Result<Expr, ParseError> {
        let mut lhs = self.primary()?;
        let mut infix_count = 0_usize;
        while self.peek() == Some(Kind::Star) {
            if infix_count >= MAX_INFIX_OPERATORS {
                return Err(ParseError {
                    offset: self.pos(),
                    class: "resource_limit",
                    message: "multiplicative expression exceeds scalar depth budget".into(),
                });
            }
            infix_count += 1;
            self.expect(Kind::Star)?;
            lhs = Expr::Multiply(Box::new(lhs), Box::new(self.primary()?));
        }
        Ok(lhs)
    }
    fn primary(&mut self) -> Result<Expr, ParseError> {
        // Unary chains recurse without parentheses and previously evaded the
        // lexer parenthesis-depth cap. Fail closed before stack exhaustion.
        if self.primary_depth >= MAX_PRIMARY_DEPTH {
            return Err(ParseError {
                offset: self.pos(),
                class: "resource_limit",
                message: "unary/call expression exceeds recursive parser budget".into(),
            });
        }
        self.primary_depth += 1;
        let parsed = self.primary_impl();
        self.primary_depth -= 1;
        parsed
    }

    fn primary_impl(&mut self) -> Result<Expr, ParseError> {
        match self.peek() {
            Some(Kind::Number) => {
                let t = self.expect(Kind::Number)?;
                let integer =
                    normalized_integer_digits(&t)?
                        .parse::<i64>()
                        .map_err(|_| ParseError {
                            offset: t.start,
                            class: "integer_overflow",
                            message: "i64 integer literal overflow".into(),
                        })?;
                Ok(Expr::Integer(integer))
            }
            Some(Kind::Identifier) => {
                let name = self.expect(Kind::Identifier)?.text;
                if self.peek() != Some(Kind::LParen) {
                    return Ok(Expr::Name(name));
                }
                self.expect(Kind::LParen)?;
                let mut args = Vec::new();
                if self.peek() != Some(Kind::RParen) {
                    loop {
                        args.push(self.expression()?);
                        if self.peek() == Some(Kind::Comma) {
                            self.expect(Kind::Comma)?;
                        } else {
                            break;
                        }
                    }
                }
                self.expect(Kind::RParen)?;
                Ok(Expr::Call { name, args })
            }
            Some(Kind::Plus) => {
                // Unary plus is a typed i64 identity, not an arithmetic
                // addition or coercion. Reuse the operand exactly once.
                self.expect(Kind::Plus)?;
                self.primary()
            }
            Some(Kind::Minus) => {
                let minus = self.expect(Kind::Minus)?;
                // The signed i64 minimum is valid even though its positive
                // magnitude is outside i64.
                if self.peek() == Some(Kind::Number) {
                    let token = self.tokens[self.cursor].clone();
                    let magnitude = normalized_integer_digits(&token)?;
                    if magnitude == "9223372036854775808" {
                        self.expect(Kind::Number)?;
                        return Ok(Expr::Integer(i64::MIN));
                    }
                }
                let expr = self.primary()?;
                if let Expr::Integer(value) = expr {
                    return value.checked_neg().map(Expr::Integer).ok_or(ParseError {
                        offset: minus.start,
                        class: "integer_overflow",
                        message: "negated literal cannot fit in i64".into(),
                    });
                }
                Ok(Expr::Negate(Box::new(expr)))
            }
            Some(Kind::LParen) => {
                self.expect(Kind::LParen)?;
                let value = self.expression()?;
                self.expect(Kind::RParen)?;
                Ok(value)
            }
            _ => Err(ParseError {
                offset: self.pos(),
                class: "syntax_error",
                message: "expected integer, name, call, or parenthesized expression".into(),
            }),
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_excessive_parenthesis_nesting_before_recursive_descent() {
        let expression = format!(
            "{}1{}",
            "(".repeat(MAX_PAREN_NESTING + 1),
            ")".repeat(MAX_PAREN_NESTING + 1)
        );
        let src = format!("fnc f(): int {{ return {expression}; }}");
        assert_eq!(parse(&src).unwrap_err().class, "resource_limit");
    }

    #[test]
    fn rejects_excessive_tokens_before_deep_ast_construction() {
        let expression = "1+".repeat(MAX_TOKENS) + "1";
        let src = format!("fnc f(): int {{ return {expression}; }}");
        assert_eq!(parse(&src).unwrap_err().class, "resource_limit");
    }

    #[test]
    fn rejects_oversized_source_before_preprocessing_comments() {
        let src = " ".repeat(MAX_SOURCE_BYTES + 1);
        assert_eq!(parse(&src).unwrap_err().class, "resource_limit");
    }

    #[test]
    fn canonical_function_call_and_underscore() {
        let ast = parse("pub fnc main(): int { return add(4_0, 2); }\nfnc add(int a, int b): int { return a+b; }").unwrap();
        assert_eq!(ast.functions[0].name, "main");
        assert_eq!(ast.functions[1].params, vec!["a", "b"]);
        assert!(matches!(ast.functions[0].expression, Expr::Call { .. }));
    }
    #[test]
    fn precedence_and_legacy_end() {
        let ast = parse("static fnc answer() as return 1 + 2 * 3; end").unwrap();
        assert!(matches!(ast.functions[0].expression, Expr::Add(..)));
    }
    #[test]
    fn nested_comments_and_minimum() {
        let ast = parse(
            "/* outer /* inner */ end */ pub fnc main(): int { return -9223372036854775808; }",
        )
        .unwrap();
        assert_eq!(ast.functions[0].expression, Expr::Integer(i64::MIN));
    }
    #[test]
    fn unclosed_comment_reports_class() {
        let err = parse("pub fnc main(): int { return 42; } /* unclosed").unwrap_err();
        assert_eq!(err.class, "unterminated_comment");
    }
    #[test]
    fn overflow_reports_class() {
        let err = parse("pub fnc main(): int { return 9223372036854775808; }").unwrap_err();
        assert_eq!(err.class, "integer_overflow");
    }
    #[test]
    fn const_locals() {
        let ast =
            parse("fnc owned(int owned): int { const local: int = owned; return local; }").unwrap();
        assert_eq!(ast.functions[0].locals.len(), 1);
    }
    #[test]
    fn raw_nul_is_denied_before_opaque_block_comment_preprocessing() {
        let source = "/* hidden\0text */ pub fnc main(): int { return 42; }";
        let error = parse(source).expect_err("a block comment must not conceal raw NUL");
        assert_eq!(error.class, "source_admission");
        assert_eq!(error.offset, source.find('\0').unwrap());
        assert_eq!(error.message, "source contains NUL byte");
    }

    #[test]
    fn raw_nul_is_denied_in_line_comments_and_between_tokens() {
        for source in [
            "// hidden\0text\npub fnc main(): int { return 42; }",
            "pub fnc main(): int { return\0 42; }",
        ] {
            let error = parse(source).expect_err("raw NUL must fail closed");
            assert_eq!(error.class, "source_admission");
            assert_eq!(error.offset, source.find('\0').unwrap());
        }
    }

    #[test]
    fn valid_utf8_comments_continue_to_compile() {
        let source = "/* café 😀 */ pub fnc main(): int { return 42; }";
        assert_eq!(
            parse(source).unwrap().functions[0].expression,
            Expr::Integer(42)
        );
    }

    #[test]
    fn ignores_double_slash_comment() {
        assert!(parse("// first\npub fnc main(): int { return 40+2; }").is_ok());
    }

    #[test]
    fn line_comment_block_opener_does_not_swallow_source() {
        let source = "// /* ignored until newline\npub fnc main(): int { return 42; }";
        let parsed = parse(source).expect("block opener inside // must be inert");
        assert_eq!(parsed.functions.len(), 1);
        assert_eq!(parsed.functions[0].expression, Expr::Integer(42));
    }

    #[test]
    fn line_comment_does_not_hide_real_unterminated_block() {
        let source = "// /* inert\n/* real unterminated block";
        let error = parse(source).expect_err("real unclosed block must still fail");
        assert_eq!(error.class, "unterminated_comment");
        assert_eq!(error.offset, source.find("\n/*").unwrap() + 1);
    }

    #[test]
    fn block_comment_does_not_turn_nested_line_markers_into_line_comments() {
        let source = "/* // /* nested */ outer */ pub fnc main(): int { return 42; }";
        let parsed = parse(source).expect("nested blocks remain valid");
        assert_eq!(parsed.functions.len(), 1);
        assert_eq!(parsed.functions[0].expression, Expr::Integer(42));
    }

    #[test]
    fn carriage_return_line_comment_is_opaque_to_block_scanner() {
        let source = "// /* not a block opener\rpub fnc main(): int { return 42; }";
        let parsed = parse(source).expect("CR terminates a line comment");
        assert_eq!(parsed.functions[0].expression, Expr::Integer(42));
    }
}
