//! Real compile() front-door regression for the bounded private dynamic
//! Result<newtype,RangeError> nominal guest profile.
use atto_compiler::compile;

const SOURCE:&str=r#"
define newtype Port(i64) where value gte 1 and value lte 65535; end
fnc create_port(int value): Result<Port,RangeError> {
  return Port.try_from(value);
}
pub fnc main(): int {
  val int input = 8080;
  val Result<Port,RangeError> checked = create_port(input);
  match checked over
    on Ok(port) -> { return port.to_base(); }
    on Err(reason) -> { return 0; }
  end
}
"#;
#[test]
fn runtime_checked_nominal_result_compiles_to_real_wasm(){
    let wasm=compile(SOURCE).expect("typed Result<Port,RangeError> should produce guest");
    assert!(wasm.starts_with(b"\0asm\x01\0\0\0"));
    assert_eq!(compile(SOURCE).unwrap(),wasm,"guest must be byte deterministic");
    assert!(wasm.len()<32768,"unexpected dynamic or hosted codegen");
}
#[test]
fn result_type_identity_is_not_structural(){
    let wrong=SOURCE.replace("val Result<Port,RangeError> checked = create_port(input);",
        "val Result<UserId,RangeError> checked = create_port(input);")
        .replace("define newtype Port(i64)","define newtype UserId(i64); define newtype Port(i64)");
    assert_eq!(compile(&wrong).unwrap_err().code,"E_NEWTYPE_MISMATCH");
}
#[test]
fn wrong_checked_result_error_parameter_fails_closed(){
    let wrong=SOURCE.replace("Result<Port,RangeError> checked",
        "Result<Port,string> checked");
    assert!(compile(&wrong).is_err());
}
#[test]
fn only_ok_value_can_be_explicitly_unwrapped(){
    let wrong=SOURCE.replace("on Err(reason) -> { return 0; }",
        "on Err(reason) -> { return reason.to_base(); }");
    assert_eq!(compile(&wrong).unwrap_err().code,"E_NEWTYPE_MISMATCH");
    let wrong=SOURCE.replace("on Ok(port) -> { return port.to_base(); }",
        "on Ok(port) -> { return port; }");
    assert!(compile(&wrong).is_err());
}
#[test]
fn full_result_elaboration_cannot_elevate_public_nominal_abi(){
    let wrong=SOURCE.replace("define newtype Port","define pub newtype Port");
    assert!(compile(&wrong).is_err());
    let wrong=SOURCE.replace("fnc create_port","pub fnc create_port");
    assert!(compile(&wrong).is_err());
}
#[test]
fn checked_constructor_is_never_used_as_an_implicit_cast(){
    let wrong=SOURCE.replace("val Result<Port,RangeError> checked = create_port(input);",
        "val Result<Port,RangeError> checked = input;");
    assert!(compile(&wrong).is_err());
}
#[test]
fn failed_to_match_a_variant_is_not_an_acceptable_guest(){
    let wrong=SOURCE.replace("on Err(reason) -> { return 0; }",
        "on Ok(reason) -> { return 0; }");
    assert!(compile(&wrong).is_err());
}
#[test]
fn unbounded_newtype_accepts_signed_extreme_constants_in_validator(){
    let source=SOURCE.replace(
        "where value gte 1 and value lte 65535; end",";")
        .replace("8080","-9223372036854775808");
    assert!(compile(&source).is_ok());
}
