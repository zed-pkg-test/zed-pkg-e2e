//! Production AST/ordinary checker admission tests: no source rewriting.
use atto_compiler::{ast::Type,check,lexer,parser::Parser};

fn parse(source:&str)->Result<atto_compiler::ast::Program,atto_compiler::diag::Diagnostic>{
    Parser::new(lexer::lex(source)?).parse_program()
}

const SOURCE:&str=r#"
define newtype Port(i64) where value gte 1 and value lte 65535; end
define newtype UserId(i64);
fnc make_port(): Result<Port,RangeError> {
  return Ok(42);
}
fnc use_port(): Result<Port,RangeError> {
  return Ok(8080);
}
pub fnc main(): int { return 0; }
"#;

#[test]
fn production_ast_keeps_nominal_declaration_and_exact_interval(){
    let p=parse(SOURCE).expect("ordinary parser must retain newtype declarations");
    assert_eq!(p.newtypes.len(),2);
    assert_eq!(p.newtypes[0].name,"Port");
    assert_eq!(p.newtypes[0].representation,Type::Int);
    assert_eq!((p.newtypes[0].low,p.newtypes[0].high),(1,65535));
    assert_eq!(p.newtypes[1].name,"UserId");
    assert_eq!((p.newtypes[1].low,p.newtypes[1].high),(i64::MIN,i64::MAX));
    assert_eq!(p.generic_results.len(),1,"identical generic arguments must intern exactly once");
    assert!(matches!(p.funcs[0].ret,Type::GenericResult(0)));
    assert!(matches!(p.funcs[1].ret,Type::GenericResult(0)));
    assert_ne!(p.funcs[2].ret,p.funcs[0].ret);
}

#[test]
fn general_production_ast_refuses_unverified_guest_error_object_layout(){
    let p=parse(SOURCE).unwrap();
    let d=check::check(&p).expect_err("generic Result cannot coerce to Result<int,string>");
    assert_eq!(d.code,"E_UNSUPPORTED_TYPE");
    assert!(d.message.contains("RangeError") || d.message.contains("ABI"));
}

#[test]
fn generic_result_arguments_do_not_erase_success_or_error_types(){
    let src=SOURCE.replace("fnc use_port(): Result<Port,RangeError>",
                           "fnc use_port(): Result<UserId,RangeError>");
    let p=parse(&src).unwrap();
    assert_eq!(p.generic_results.len(),2);
    assert_ne!(p.funcs[0].ret,p.funcs[1].ret);
    let err=SOURCE.replace("fnc use_port(): Result<Port,RangeError>",
                           "fnc use_port(): Result<Port,Error>");
    let p=parse(&err).unwrap();
    assert_eq!(p.generic_results.len(),2);
    assert_ne!(p.funcs[0].ret,p.funcs[1].ret);
    assert_eq!(check::check(&p).unwrap_err().code,"E_UNSUPPORTED_TYPE");
}

#[test]
fn generic_result_bad_success_nominal_has_specific_type_error(){
    let src=SOURCE.replace("Result<Port,RangeError>","Result<Missing,RangeError>");
    let p=parse(&src).unwrap();
    assert_eq!(check::check(&p).unwrap_err().code,"E_UNKNOWN_TYPE");
}

#[test]
fn duplicate_nominal_type_declaration_is_type_error(){
    let src=SOURCE.replace("define newtype UserId(i64);","define newtype Port(i64);");
    let p=parse(&src).unwrap();
    assert_eq!(check::check(&p).unwrap_err().code,"E_DUPLICATE");
}

#[test]
fn unproven_predicates_and_empty_intervals_never_become_ast_types(){
    let empty=SOURCE.replace("value gte 1 and value lte 65535",
            "value gte 10 and value lte 9");
    assert_eq!(parse(&empty).unwrap_err().code,"E_RANGE_INVALID");
    let dynamic=SOURCE.replace("value gte 1 and value lte 65535",
            "value gte runtime_variable");
    assert_eq!(parse(&dynamic).unwrap_err().code,"E_RANGE_UNPROVEN");
}

#[test]
fn signed_extrema_are_exact_and_public_abi_is_gated(){
    let src=SOURCE.replace("value gte 1 and value lte 65535",
            "value gte -9223372036854775808 and value lte 9223372036854775807");
    let p=parse(&src).unwrap();
    assert_eq!((p.newtypes[0].low,p.newtypes[0].high),(i64::MIN,i64::MAX));
    let exposed=SOURCE.replace("define newtype Port","pub define newtype Port");
    let p=parse(&exposed).unwrap();
    assert_eq!(check::check(&p).unwrap_err().code,"E_UNSUPPORTED_TYPE");
}

#[test]
fn unsupported_generic_result_with_no_nominal_still_rejected_before_codegen(){
    let p=parse("fnc f(): Result<int,bool> { return Ok(0); }\npub fnc main(): int { return 0; }").unwrap();
    assert!(matches!(p.funcs[0].ret,Type::GenericResult(0)));
    assert_eq!(check::check(&p).unwrap_err().code,"E_UNSUPPORTED_TYPE");
}
