//! Executable source-local i64 nominal constant profile.
//! Parse and typecheck every accepted operation before converting to the
//! normal production scalar frontend; no unchecked nominal value erasure.
//! Dynamic try_from, public ABI, mutable values and generic arrays fail closed.
use std::collections::BTreeMap;
use crate::diag::{Diagnostic, Pos};
use crate::lexer::{self, Tok, Token};
use crate::newtype_const_generic_admission::{self, Interval};

type R<T> = Result<T, Diagnostic>;
const MAX_FUNCS: usize = 32;
const MAX_STMTS: usize = 128;
const MAX_EXPRESSION_DEPTH: usize = 24;

#[derive(Clone)]
struct Signature { param: Option<String>, ret: String }
struct Value { ty: String, lower: String }
struct Cur<'a> { t: &'a [Token], i: usize }
impl Cur<'_> {
    fn at(&self, off: usize) -> Option<&Tok> { self.t.get(self.i+off).map(|x|&x.tok) }
    fn word(&self, off: usize) -> Option<&str> {
        match self.at(off) { Some(Tok::Ident(s)) => Some(s), _ => None }
    }
    fn err(&self, code: &'static str, why: &str) -> Diagnostic {
        Diagnostic::new(self.t.get(self.i).map_or(Pos::default(),|x|x.pos),code,why)
    }
    fn eat(&mut self, kind: Tok) -> bool {
        if self.at(0)==Some(&kind) { self.i+=1; true } else { false }
    }
    fn expect(&mut self, kind: Tok) -> R<()> {
        if self.eat(kind) { Ok(()) }
        else { Err(self.err("E_UNSUPPORTED","closed newtype guest grammar required")) }
    }
    fn word_eat(&mut self, w: &str) -> bool {
        if self.word(0)==Some(w) { self.i+=1; true } else { false }
    }
    fn name(&mut self) -> R<String> {
        let name=self.word(0).ok_or_else(||self.err("E_UNSUPPORTED","identifier expected"))?;
        if !name.is_ascii() || name.len()>96 || crate::parser::is_reserved(name) ||
            matches!(name,"newtype"|"where"|"int"|"i64"|"from_const"|"try_from"|"to_base") {
            return Err(self.err("E_UNSUPPORTED","unsupported or reserved identifier"));
        }
        let name=name.to_string(); self.i+=1; Ok(name)
    }
    fn ty(&mut self, names: &BTreeMap<String,Interval>) -> R<String> {
        if self.word_eat("int") || self.word_eat("i64") { return Ok("int".into()); }
        let v=self.name()?;
        if names.contains_key(&v) { Ok(v) }
        else { Err(self.err("E_UNSUPPORTED_TYPE","unknown nominal type")) }
    }
}
fn integer(c: &mut Cur<'_>) -> R<i64> {
    let neg=c.eat(Tok::Minus);
    let val=match c.at(0) {
        Some(Tok::Int(Some(n))) if *n<=i64::MAX as u64 =>
            if neg {-(*n as i64)} else {*n as i64},
        Some(Tok::Int(None)) if neg => i64::MIN,
        _ => return Err(c.err("E_RANGE_UNPROVEN","compile-time exact i64 literal required"))
    };
    c.i+=1; Ok(val)
}
fn compatible(c: &Cur<'_>,expected:&str,actual:&str)->R<()>{
    if expected==actual{return Ok(());}
    let code=if expected!="int" && actual=="int" {"E_NEWTYPE_CONSTRUCTION"}
        else {"E_NEWTYPE_MISMATCH"};
    Err(c.err(code,"opaque nominal identity cannot be implicitly converted"))
}
fn expression(c:&mut Cur<'_>,names:&BTreeMap<String,Interval>,
    locals:&BTreeMap<String,String>,funcs:&BTreeMap<String,Signature>, depth:usize)->R<Value>
{
    if depth>MAX_EXPRESSION_DEPTH {return Err(c.err("E_SOURCE_LIMIT","nested nominal call budget exceeded"));}
    if matches!(c.at(0),Some(Tok::Int(_))|Some(Tok::Minus)){
        return Ok(Value{ty:"int".into(),lower:integer(c)?.to_string()});
    }
    let name=c.name()?;
    if c.eat(Tok::Dot){
        if c.word_eat("from_const") && names.contains_key(&name){
            c.expect(Tok::LParen)?;
            let v=integer(c)?;
            c.expect(Tok::RParen)?;
            if !names[&name].contains(v){
                return Err(c.err("E_RANGE_INVALID","constant violates nominal range"));
            }
            return Ok(Value{ty:name,lower:v.to_string()});
        }
        if c.word_eat("to_base") && locals.contains_key(&name){
            c.expect(Tok::LParen)?; c.expect(Tok::RParen)?;
            if locals[&name]=="int" {return Err(c.err("E_NEWTYPE_CONSTRUCTION","not a newtype"));}
            return Ok(Value{ty:"int".into(),lower:name});
        }
        return Err(c.err("E_UNSUPPORTED","only from_const and local to_base are supported"));
    }
    if c.eat(Tok::LParen){
        let sig=funcs.get(&name).ok_or_else(||c.err("E_UNSUPPORTED",
            "recursive, forward or unknown nominal helper call"))?;
        let param=sig.param.as_ref().ok_or_else(||c.err("E_UNSUPPORTED","helper requires one parameter"))?;
        let arg=expression(c,names,locals,funcs,depth+1)?;
        c.expect(Tok::RParen)?;
        compatible(c,param,&arg.ty)?;
        return Ok(Value{ty:sig.ret.clone(),lower:format!("{name}({})",arg.lower)});
    }
    let ty=locals.get(&name).ok_or_else(||c.err("E_UNSUPPORTED","unknown nominal variable"))?;
    Ok(Value{ty:ty.clone(),lower:name})
}
fn declaration(c:&mut Cur<'_>)->R<()>{
    c.expect(Tok::LParen)?;
    if !c.word_eat("i64") && !c.word_eat("int") {
        return Err(c.err("E_UNSUPPORTED_TYPE","only signed i64 newtypes supported"));
    }
    c.expect(Tok::RParen)?;
    if c.eat(Tok::Semi){return Ok(());}
    if !c.word_eat("where"){return Err(c.err("E_UNSUPPORTED","malformed nominal declaration"));}
    let mut comparisons=0;
    loop{
        if !c.word_eat("value"){return Err(c.err("E_RANGE_UNPROVEN","unsupported predicate"));}
        if !(c.word_eat("gte")||c.word_eat("lte")||c.eat(Tok::Ge)||c.eat(Tok::Le)){
            return Err(c.err("E_RANGE_UNPROVEN","unsupported comparator"));
        }
        integer(c)?;
        comparisons+=1;
        if comparisons>16{return Err(c.err("E_SOURCE_LIMIT","too many bounds"));}
        if !c.word_eat("and"){break;}
    }
    c.expect(Tok::Semi)?;
    if !c.word_eat("end"){return Err(c.err("E_UNSUPPORTED","bounds require end"));}
    c.eat(Tok::Semi);
    Ok(())
}
/// Returns a fully checked, lowered scalar token stream only for a closed
/// local-program profile. All other forms retain the caller's fail-closed gate.
pub fn try_lower(tokens:&[Token])->R<Option<Vec<Token>>>{
    let admission=newtype_const_generic_admission::inspect(tokens)?;
    if admission.nominal.is_empty(){return Ok(None);}
    if !admission.known_lengths.is_empty() || tokens.windows(2).any(|w|
        matches!((&w[0].tok,&w[1].tok),
            (Tok::Ident(s),Tok::Lt) if s=="Array") ||
        matches!((&w[0].tok,&w[1].tok),
            (Tok::Ident(s),Tok::Ident(k)) if s=="const" && k=="usize"))
    {return Ok(None);}
    let mut c=Cur{t:tokens,i:0};
    let mut emitted=String::new();
    let mut funcs=BTreeMap::<String,Signature>::new();
    let mut saw_main=false;
    let mut count=0;
    while c.at(0)!=Some(&Tok::Eof) && c.at(0).is_some(){
        if c.word_eat("define"){
            if c.word(0)==Some("pub"){return Ok(None);}
            if !c.word_eat("newtype"){return Ok(None);}
            let n=c.name()?;
            if !admission.nominal.contains_key(&n){return Ok(None);}
            declaration(&mut c)?;
            continue;
        }
        let public=c.word_eat("pub");
        if !c.word_eat("fnc"){return Ok(None);}
        let name=c.name()?;
        count+=1;
        if count>MAX_FUNCS || saw_main || funcs.contains_key(&name){return Ok(None);}
        c.expect(Tok::LParen)?;
        let param=if c.eat(Tok::RParen){None}else{
            let t=c.ty(&admission.nominal)?;
            let n=c.name()?;
            c.expect(Tok::RParen)?;
            Some((t,n))
        };
        if !c.eat(Tok::Colon) && !c.eat(Tok::Arrow){return Ok(None);}
        let ret=c.ty(&admission.nominal)?;
        if public!=(name=="main") ||
            (name=="main" && (param.is_some() || ret!="int")) ||
            (name!="main" && param.is_none()) {return Ok(None);}
        c.expect(Tok::LBrace)?;
        let mut locals=BTreeMap::<String,String>::new();
        if let Some((ref t,ref n))=param {locals.insert(n.clone(),t.clone());}
        let mut body=String::new();
        let mut statements=0;
        let mut returned=false;
        while !c.eat(Tok::RBrace){
            statements+=1;
            if statements>MAX_STMTS || c.at(0)==Some(&Tok::Eof){
                return Err(c.err("E_SOURCE_LIMIT","nominal body too long or incomplete"));
            }
            if c.word_eat("val"){
                if returned{return Ok(None);}
                let t=c.ty(&admission.nominal)?;
                let n=c.name()?;
                if locals.contains_key(&n){return Ok(None);}
                c.expect(Tok::Assign)?;
                let e=expression(&mut c,&admission.nominal,&locals,&funcs,0)?;
                compatible(&c,&t,&e.ty)?;
                c.expect(Tok::Semi)?;
                body.push_str(&format!("val int {n} = {};\n",e.lower));
                locals.insert(n,t);
            }else if c.word_eat("return"){
                if returned{return Ok(None);}
                let e=expression(&mut c,&admission.nominal,&locals,&funcs,0)?;
                compatible(&c,&ret,&e.ty)?;
                c.expect(Tok::Semi)?;
                if c.at(0)!=Some(&Tok::RBrace){return Ok(None);}
                body.push_str(&format!("return {};\n",e.lower));
                returned=true;
            }else{return Ok(None);}
        }
        if !returned{return Ok(None);}
        let args=param.as_ref().map(|(_,n)|format!("int {n}")).unwrap_or_default();
        emitted.push_str(&format!("{}fnc {name}({args}): int {{\n{body}}}\n",
            if public{"pub "}else{""}));
        funcs.insert(name.clone(),Signature{param:param.as_ref().map(|(t,_)|t.clone()),ret});
        if name=="main"{saw_main=true;}
    }
    if !saw_main || count==0{return Ok(None);}
    // Do not claim executable nominal support for a declaration that is
    // never constructed. Such programs stay on the original fail-closed path.
    let has_checked_construction = tokens.windows(4).any(|w|
        matches!((&w[0].tok,&w[1].tok,&w[2].tok,&w[3].tok),
            (Tok::Ident(name),Tok::Dot,Tok::Ident(method),Tok::LParen)
                if admission.nominal.contains_key(name) && method=="from_const"));
    if !has_checked_construction { return Ok(None); }
    if emitted.len()>crate::SOURCE_CAP {return Err(c.err("E_SOURCE_LIMIT","expanded nominal guest exceeds source size budget"));}
    Ok(Some(lexer::lex(&emitted)?))
}
