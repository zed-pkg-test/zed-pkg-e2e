//! Closed-world, runtime-checked scalar newtype Result guest profile.
//! Source is nominally checked BEFORE it privately lowers to production
//! Result<int,string> and the real typed WebAssembly checker/emitter.
//! The private Err payload is "RangeError", an inaccessible singleton marker;
//! no public Error object, public ABI, general Result<T,E> or guest JIT.
use crate::diag::{Diagnostic, Pos};
use crate::lexer::{self, Tok, Token};
use crate::newtype_const_generic_admission::{self, Interval};
use crate::newtype_typed_ir::{NominalGraph, Ty};
use std::collections::{BTreeMap, BTreeSet};
type R<T> = Result<T, Diagnostic>;
const MAX_FUNCS: usize = 32;
const MAX_VARS: usize = 128;

struct TypedValue {
    ty: Ty,
    lower: String,
}
struct Helper {
    nominal: String,
}
struct Cur<'a> {
    ts: &'a [Token],
    i: usize,
}
impl Cur<'_> {
    fn tok(&self, n: usize) -> Option<&Tok> {
        self.ts.get(self.i + n).map(|x| &x.tok)
    }
    fn word(&self, n: usize) -> Option<&str> {
        match self.tok(n) {
            Some(Tok::Ident(s)) => Some(s),
            _ => None,
        }
    }
    fn pos(&self) -> Pos {
        self.ts.get(self.i).map_or(Pos::default(), |t| t.pos)
    }
    fn err(&self, code: &'static str, msg: &str) -> Diagnostic {
        Diagnostic::new(self.pos(), code, msg)
    }
    fn eat(&mut self, token: Tok) -> bool {
        if self.tok(0) == Some(&token) {
            self.i += 1;
            true
        } else {
            false
        }
    }
    fn require(&mut self, token: Tok) -> R<()> {
        if self.eat(token) {
            Ok(())
        } else {
            Err(self.err(
                "E_UNSUPPORTED",
                "closed nominal runtime-result grammar required",
            ))
        }
    }
    fn eat_word(&mut self, word: &str) -> bool {
        if self.word(0) == Some(word) {
            self.i += 1;
            true
        } else {
            false
        }
    }
    fn require_word(&mut self, w: &str) -> R<()> {
        if self.eat_word(w) {
            Ok(())
        } else {
            Err(self.err(
                "E_UNSUPPORTED",
                "unexpected keyword in checked nominal guest profile",
            ))
        }
    }
    fn name(&mut self) -> R<String> {
        let s = self
            .word(0)
            .ok_or_else(|| self.err("E_UNSUPPORTED", "identifier expected"))?;
        let valid = s.len() <= 96
            && !s.starts_with("__atto_")
            && s.chars().enumerate().all(|(i, ch)| {
                ch.is_ascii_alphabetic() || ch == '_' || (i > 0 && ch.is_ascii_digit())
            });
        if !valid
            || crate::parser::is_reserved(s)
            || matches!(
                s,
                "Result" | "RangeError" | "from_const" | "try_from" | "to_base"
            )
        {
            return Err(self.err(
                "E_UNSUPPORTED",
                "reserved or invalid checked nominal identifier",
            ));
        }
        let name = s.to_string();
        self.i += 1;
        Ok(name)
    }
    fn ty(&mut self, n: &BTreeMap<String, Interval>) -> R<Ty> {
        if self.eat_word("int") || self.eat_word("i64") {
            return Ok(Ty::I64);
        }
        if self.eat_word("Result") {
            self.require(Tok::Lt)?;
            let nominal = self.name()?;
            if !n.contains_key(&nominal) {
                return Err(self.err("E_UNSUPPORTED_TYPE", "unknown Result success newtype"));
            }
            self.require(Tok::Comma)?;
            self.require_word("RangeError")?;
            self.require(Tok::Gt)?;
            return Ok(Ty::checked(nominal));
        }
        let name = self.name()?;
        if !n.contains_key(&name) {
            return Err(self.err("E_UNSUPPORTED_TYPE", "unknown nominal type"));
        }
        Ok(Ty::Nominal(name))
    }
}
fn same(c: &Cur<'_>, want: &Ty, got: &Ty) -> R<()> {
    if want == got {
        return Ok(());
    }
    Err(c.err(
        if matches!((want, got), (Ty::Nominal(_), Ty::I64)) {
            "E_NEWTYPE_CONSTRUCTION"
        } else {
            "E_NEWTYPE_MISMATCH"
        },
        "nominal Result and newtype identities must match",
    ))
}
fn integer(c: &mut Cur<'_>) -> R<i64> {
    let negative = c.eat(Tok::Minus);
    let v = match c.tok(0) {
        Some(Tok::Int(Some(n))) if *n <= i64::MAX as u64 => {
            if negative {
                -(*n as i64)
            } else {
                *n as i64
            }
        }
        Some(Tok::Int(None)) if negative => i64::MIN,
        _ => return Err(c.err("E_RANGE_UNPROVEN", "exact signed i64 required")),
    };
    c.i += 1;
    Ok(v)
}
fn decl(c: &mut Cur<'_>) -> R<()> {
    c.require(Tok::LParen)?;
    if !c.eat_word("i64") && !c.eat_word("int") {
        return Err(c.err("E_UNSUPPORTED_TYPE", "only i64 newtypes executable"));
    }
    c.require(Tok::RParen)?;
    if c.eat(Tok::Semi) {
        return Ok(());
    }
    c.require_word("where")?;
    let mut count = 0;
    loop {
        c.require_word("value")?;
        if !(c.eat_word("gte") || c.eat_word("lte") || c.eat(Tok::Ge) || c.eat(Tok::Le)) {
            return Err(c.err("E_RANGE_UNPROVEN", "unsupported newtype interval predicate"));
        }
        integer(c)?;
        count += 1;
        if count > 16 {
            return Err(c.err(
                "E_SOURCE_LIMIT",
                "newtype interval predicate budget exceeded",
            ));
        }
        if !c.eat_word("and") {
            break;
        }
    }
    c.require(Tok::Semi)?;
    c.require_word("end")?;
    c.eat(Tok::Semi);
    Ok(())
}
fn validator_name(n: &str) -> String {
    format!("__atto_checked_newtype_{n}")
}
fn validator(n: &str, span: Interval) -> String {
    let mut text = format!(
        "fnc {}(int value): Result<int,string> {{\n",
        validator_name(n)
    );
    if span.low != i64::MIN {
        text.push_str(&format!(
            "if value < {} then return Err(\"RangeError\"); fi\n",
            span.low
        ));
    }
    if span.high != i64::MAX {
        text.push_str(&format!(
            "if value > {} then return Err(\"RangeError\"); fi\n",
            span.high
        ));
    }
    text.push_str("return Ok(value);\n}\n");
    text
}
fn int_arg(c: &mut Cur<'_>, vars: &BTreeMap<String, Ty>) -> R<String> {
    if matches!(c.tok(0), Some(Tok::Int(_)) | Some(Tok::Minus)) {
        return Ok(integer(c)?.to_string());
    }
    let name = c.name()?;
    let ty = vars.get(&name).ok_or_else(|| {
        c.err(
            "E_UNSUPPORTED",
            "runtime source must be a declared initialized integer",
        )
    })?;
    same(c, &Ty::I64, ty)?;
    Ok(name)
}
fn checked_expr(
    c: &mut Cur<'_>,
    types: &BTreeMap<String, Interval>,
    vars: &BTreeMap<String, Ty>,
    helpers: &BTreeMap<String, Helper>,
) -> R<TypedValue> {
    let name = c.name()?;
    if c.eat(Tok::Dot) {
        c.require_word("try_from")?;
        if !types.contains_key(&name) {
            return Err(c.err("E_UNSUPPORTED_TYPE", "unknown runtime nominal constructor"));
        }
        c.require(Tok::LParen)?;
        let v = int_arg(c, vars)?;
        c.require(Tok::RParen)?;
        return Ok(TypedValue {
            ty: Ty::checked(name.clone()),
            lower: format!("{}({v})", validator_name(&name)),
        });
    }
    if c.eat(Tok::LParen) {
        let helper = helpers.get(&name).ok_or_else(|| {
            c.err(
                "E_UNSUPPORTED",
                "unknown or forward checked constructor helper",
            )
        })?;
        let v = int_arg(c, vars)?;
        c.require(Tok::RParen)?;
        return Ok(TypedValue {
            ty: Ty::checked(helper.nominal.clone()),
            lower: format!("{name}({v})"),
        });
    }
    let ty = vars
        .get(&name)
        .ok_or_else(|| c.err("E_UNSUPPORTED", "unknown checked-result binding"))?;
    if ty.checked_nominal().is_none() {
        return Err(c.err(
            "E_NEWTYPE_MISMATCH",
            "only checked Result bindings are allowed here",
        ));
    }
    Ok(TypedValue {
        ty: ty.clone(),
        lower: name,
    })
}
fn helper(
    c: &mut Cur<'_>,
    name: String,
    types: &BTreeMap<String, Interval>,
    funcs: &mut BTreeMap<String, Helper>,
    code: &mut String,
) -> R<()> {
    c.require(Tok::LParen)?;
    let parsed_arg_type = c.ty(types)?;
    same(c, &Ty::I64, &parsed_arg_type)?;
    let arg = c.name()?;
    c.require(Tok::RParen)?;
    c.require(Tok::Colon)?;
    let ret = c.ty(types)?;
    let Some(nominal) = ret.checked_nominal().map(str::to_owned) else {
        return Err(c.err(
            "E_UNSUPPORTED_TYPE",
            "helper must return Result<Newtype,RangeError>",
        ));
    };
    c.require(Tok::LBrace)?;
    c.require_word("return")?;
    let vars = BTreeMap::from([(arg.clone(), Ty::I64)]);
    let result = checked_expr(c, types, &vars, funcs)?;
    same(c, &Ty::checked(nominal.clone()), &result.ty)?;
    c.require(Tok::Semi)?;
    c.require(Tok::RBrace)?;
    if funcs.insert(name.clone(), Helper { nominal }).is_some() {
        return Err(c.err("E_DUPLICATE", "checked constructor helper is duplicated"));
    }
    code.push_str(&format!(
        "fnc {name}(int {arg}): Result<int,string> {{ return {}; }}\n",
        result.lower
    ));
    Ok(())
}
fn arm(c: &mut Cur<'_>, variant: &str, nominal: &str, expression: bool, last: bool) -> R<String> {
    c.require_word("on")?;
    c.require_word(variant)?;
    c.require(Tok::LParen)?;
    let bind = if c.word(0) == Some("_") {
        c.i += 1;
        if variant == "Ok" {
            "__atto_unused_ok".to_string()
        } else {
            "__atto_unused_err".to_string()
        }
    } else {
        c.name()?
    };
    c.require(Tok::RParen)?;
    c.require(Tok::Arrow)?;
    if !expression {
        c.require(Tok::LBrace)?;
        c.require_word("return")?;
    }
    let result = if matches!(c.tok(0), Some(Tok::Int(_)) | Some(Tok::Minus)) {
        integer(c)?.to_string()
    } else {
        let referent = c.name()?;
        if variant != "Ok" || referent != bind || nominal.is_empty() {
            return Err(c.err(
                "E_NEWTYPE_MISMATCH",
                "only the bound Ok newtype may be unwrapped",
            ));
        }
        c.require(Tok::Dot)?;
        c.require_word("to_base")?;
        c.require(Tok::LParen)?;
        c.require(Tok::RParen)?;
        bind.clone()
    };
    if expression {
        if last {
            c.eat(Tok::Comma);
        } else {
            c.require(Tok::Comma)?;
        }
    } else {
        c.require(Tok::Semi)?;
        c.require(Tok::RBrace)?;
    }
    Ok(format!("on {variant}({bind}) -> {{ return {result}; }}\n"))
}
fn main_body(
    c: &mut Cur<'_>,
    types: &BTreeMap<String, Interval>,
    helpers: &BTreeMap<String, Helper>,
    code: &mut String,
) -> R<()> {
    c.require(Tok::LParen)?;
    c.require(Tok::RParen)?;
    c.require(Tok::Colon)?;
    let parsed_arg_type = c.ty(types)?;
    same(c, &Ty::I64, &parsed_arg_type)?;
    c.require(Tok::LBrace)?;
    let mut vars = BTreeMap::<String, Ty>::new();
    let mut lowered = String::new();
    let mut count = 0;
    while c.eat_word("val") {
        count += 1;
        if count > MAX_VARS {
            return Err(c.err("E_SOURCE_LIMIT", "checked result locals budget exceeded"));
        }
        let ty = c.ty(types)?;
        let name = c.name()?;
        if vars.contains_key(&name) {
            return Err(c.err("E_UNSUPPORTED", "no scope shadowing"));
        }
        c.require(Tok::Assign)?;
        let init = match &ty {
            Ty::I64 => TypedValue {
                ty: Ty::I64,
                lower: int_arg(c, &vars)?,
            },
            Ty::Result { .. } => checked_expr(c, types, &vars, helpers)?,
            Ty::Nominal(_) | Ty::Error(_) => {
                return Err(c.err("E_UNSUPPORTED", "unwrapped nominal/error local requires owned runtime layout"))
            }
        };
        same(c, &ty, &init.ty)?;
        c.require(Tok::Semi)?;
        let repr = ty.private_wasm_lowered_type().ok_or_else(|| {
            c.err("E_UNSUPPORTED_TYPE", "no verified guest layout for this generic Result")
        })?;
        lowered.push_str(&format!("val {name}: {repr} = {};\n", init.lower));
        vars.insert(name, ty);
    }
    let expression_match = c.eat_word("return");
    c.require_word("match")?;
    let subject = c.name()?;
    let type_of_subject = vars.get(&subject).ok_or_else(|| {
        c.err(
            "E_UNSUPPORTED",
            "Result match must reference a checked Result binding",
        )
    })?;
    let Some(nominal) = type_of_subject.checked_nominal() else {
        return Err(c.err("E_NEWTYPE_MISMATCH", "match subject is not checked Result"));
    };
    c.require_word("over")?;
    let (first, second) = match c.word(1) {
        Some("Ok") => ("Ok", "Err"),
        Some("Err") => ("Err", "Ok"),
        _ => return Err(c.err("E_MATCH", "match must cover Ok and Err")),
    };
    let a = arm(c, first, nominal, expression_match, false)?;
    let b = arm(c, second, nominal, expression_match, true)?;
    c.require_word("end")?;
    c.eat(Tok::Semi);
    c.require(Tok::RBrace)?;
    lowered.push_str(&format!("match {subject} over\n{a}{b}end\n"));
    code.push_str(&format!("pub fnc main(): int {{\n{lowered}}}\n"));
    Ok(())
}

/// Strictly private nominal typechecking prior to producing code.
pub fn try_lower(tokens: &[Token]) -> R<Option<Vec<Token>>> {
    let admitted = newtype_const_generic_admission::inspect(tokens)?;
    if admitted.nominal.is_empty() {
        return Ok(None);
    }
    if !tokens.windows(3).any(|w| {
        matches!(
        (&w[0].tok,&w[1].tok,&w[2].tok),
        (Tok::Ident(_),Tok::Dot,Tok::Ident(f)) if f=="try_from")
    }) {
        return Ok(None);
    }
    if tokens.windows(2).any(|w| {
        matches!((&w[0].tok,&w[1].tok),(Tok::Ident(x),Tok::Lt) if x=="Array")
            || matches!((&w[0].tok,&w[1].tok),
            (Tok::Ident(x),Tok::Ident(y)) if x=="const"&&y=="usize")
    }) {
        return Ok(None);
    }
    let mut cur = Cur { ts: tokens, i: 0 };
    let mut code = String::new();
    let mut helpers = BTreeMap::<String, Helper>::new();
    let mut checked_types = NominalGraph::default();
    for (name, interval) in &admitted.nominal {
        checked_types.declare("<private-module>", name, *interval, cur.pos())?;
    }
    let mut definitions = BTreeSet::new();
    let mut functions = 0;
    let mut main = false;
    while cur.tok(0) != Some(&Tok::Eof) && cur.tok(0).is_some() {
        if cur.eat_word("define") {
            if !cur.eat_word("newtype") {
                return Ok(None);
            }
            let n = cur.name()?;
            if !admitted.nominal.contains_key(&n) || !definitions.insert(n) {
                return Ok(None);
            }
            decl(&mut cur)?;
            continue;
        }
        let public = cur.eat_word("pub");
        if !cur.eat_word("fnc") {
            return Ok(None);
        }
        let name = cur.name()?;
        functions += 1;
        if functions > MAX_FUNCS || main {
            return Ok(None);
        }
        if public && name == "main" {
            main_body(&mut cur, &admitted.nominal, &helpers, &mut code)?;
            main = true;
        } else if !public && name != "main" {
            helper(&mut cur, name, &admitted.nominal, &mut helpers, &mut code)?;
        } else {
            return Ok(None);
        }
    }
    if !main || definitions.len() != admitted.nominal.len() {
        return Ok(None);
    }
    // This profile cannot invent a nominal declaration or substitute a
    // different module owner after the semantic type graph is constructed.
    for name in &definitions {
        let id = crate::newtype_typed_ir::NominalIdentity {
            module: "<private-module>".to_owned(), name: name.clone(),
        };
        if checked_types.get(&id).is_none() {
            return Err(cur.err("E_NEWTYPE_IDENTITY", "missing nominal identity"));
        }
    }
    let mut constructors = String::new();
    for (name, bounds) in &admitted.nominal {
        constructors.push_str(&validator(name, *bounds));
    }
    constructors.push_str(&code);
    if constructors.len() > crate::SOURCE_CAP {
        return Err(cur.err(
            "E_SOURCE_LIMIT",
            "checked result specialization exceeds source limit",
        ));
    }
    Ok(Some(lexer::lex(&constructors)?))
}
