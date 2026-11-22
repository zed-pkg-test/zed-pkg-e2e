//! Full compiler front-door tests for parameterized semantic Result types
//! and the closed compiled guest return-match profile.
use atto_compiler::{
    compile,
    newtype_typed_ir::{ErrorClass, NominalGraph, Ty},
};
use atto_compiler::{diag::Pos, newtype_const_generic_admission::Interval};

const RETURN_MATCH: &str = r#"
define newtype Port(i64) where value gte 1 and value lte 65535; end
fnc create_port(int value): Result<Port,RangeError> {
 return Port.try_from(value);
}
pub fnc main(): int {
 val int input = 8080;
 val Result<Port,RangeError> result = create_port(input);
 return match result over
   on Ok(port) -> port.to_base(),
   on Err(reason) -> 0
 end;
}
"#;

#[test]
fn compiler_runs_full_aot_with_nominal_result_return_match() {
    let bytes = compile(RETURN_MATCH).expect("typed Result return match must compile");
    assert!(bytes.starts_with(b"\0asm\x01\0\0\0"));
    assert_eq!(bytes, compile(RETURN_MATCH).unwrap());
}
#[test]
fn reverse_arm_order_is_exhaustive_but_duplicate_is_rejected() {
    let reversed = RETURN_MATCH.replace(
        "on Ok(port) -> port.to_base(),\n   on Err(reason) -> 0",
        "on Err(reason) -> 0,\n   on Ok(port) -> port.to_base()",
    );
    assert!(compile(&reversed).is_ok());
    let duplicated = RETURN_MATCH.replace("on Err(reason) -> 0", "on Ok(reason) -> 0");
    assert!(compile(&duplicated).is_err());
}
#[test]
fn expression_match_comma_is_mandatory_between_arms() {
    let no_comma = RETURN_MATCH.replace("port.to_base(),", "port.to_base()");
    assert!(compile(&no_comma).is_err());
}
#[test]
fn expression_match_cannot_return_opaque_nominal_without_unwrap() {
    let implicit = RETURN_MATCH.replace("port.to_base(),", "port,");
    assert!(compile(&implicit).is_err());
}
#[test]
fn result_error_supertype_is_not_a_substitution() {
    let broaden = RETURN_MATCH.replace(
        "Result<Port,RangeError> result",
        "Result<Port,Error> result",
    );
    assert!(compile(&broaden).is_err());
    let wrong = RETURN_MATCH
        .replace(
            "Result<Port,RangeError> result",
            "Result<UserId,RangeError> result",
        )
        .replace(
            "define newtype Port(i64)",
            "define newtype UserId(i64); define newtype Port(i64)",
        );
    assert_eq!(compile(&wrong).unwrap_err().code, "E_NEWTYPE_MISMATCH");
}
#[test]
fn no_public_nominal_or_error_result_abi_escape() {
    assert!(
        compile(&RETURN_MATCH.replace("define newtype Port", "define pub newtype Port")).is_err()
    );
    assert!(compile(&RETURN_MATCH.replace("fnc create_port", "pub fnc create_port")).is_err());
}
#[test]
fn internal_type_graph_proves_nominal_scope_and_range_error_subclass() {
    let mut g = NominalGraph::default();
    let p = Pos { line: 1, col: 1 };
    let a = g
        .declare(
            "+pkgA",
            "Port",
            Interval {
                low: 1,
                high: 65535,
            },
            p,
        )
        .unwrap();
    let b = g
        .declare(
            "+pkgB",
            "Port",
            Interval {
                low: 1,
                high: 65535,
            },
            p,
        )
        .unwrap();
    assert!(!g.identical(&a, &b));
    assert!(ErrorClass::RangeError.is_subclass_of(&ErrorClass::Error));
    assert_ne!(Ty::checked("Port".into()), Ty::checked("UserId".into()));
    assert_eq!(Ty::checked("Port".into()).checked_nominal(), Some("Port"));
}

fn propagated_source() -> String {
    RETURN_MATCH
        .replace(
            "pub fnc main(): int {",
            "fnc relay_port(int value): Result<Port,RangeError> {\n\
         val Port port = create_port(value)?;\n\
         return Ok(port);\n\
         }\n\
         pub fnc main(): int {",
        )
        .replace("create_port(input);", "relay_port(input);")
}

#[test]
fn checked_nominal_question_mark_success_and_error_compile_to_guest() {
    let program = propagated_source();
    let output =
        compile(&program).expect("checked nominal ? must use production typed ADT propagation");
    assert!(output.starts_with(b"\0asm\x01\0\0\0"));
    assert_eq!(output, compile(&program).unwrap());
}

#[test]
fn checked_nominal_propagation_rejects_different_nominal_type() {
    let program = propagated_source()
        .replace(
            "define newtype Port(i64)",
            "define newtype UserId(i64); define newtype Port(i64)",
        )
        .replace(
            "val Port port = create_port(value)?;",
            "val UserId port = create_port(value)?;",
        );
    assert_eq!(compile(&program).unwrap_err().code, "E_NEWTYPE_MISMATCH");
}

#[test]
fn checked_nominal_propagation_refuses_unwrapped_result_and_wrong_return() {
    let base = propagated_source();
    let no_question = base.replace("create_port(value)?;", "create_port(value);");
    assert!(compile(&no_question).is_err());
    let incorrect_return = base.replace("return Ok(port);", "return Ok(value);");
    assert_eq!(
        compile(&incorrect_return).unwrap_err().code,
        "E_NEWTYPE_MISMATCH"
    );
}
