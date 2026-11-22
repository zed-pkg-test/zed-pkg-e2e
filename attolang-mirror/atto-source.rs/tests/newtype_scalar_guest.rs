//! The real entrypoint must produce executable, nominally checked scalar
//! guests; unsupported Result/ABI/dynamic cases must emit no Wasm.
use atto_compiler::compile;

const SIMPLE: &str = r#"
define newtype UserId(i64);
fnc identity(UserId id): UserId { return id; }
fnc to_int(UserId id): int { return id.to_base(); }
pub fnc main(): int {
    val UserId id = UserId.from_const(42);
    return to_int(identity(id));
}
"#;

#[test]
fn scalar_nominal_helpers_emit_real_wasm_without_boxing() {
    let guest=compile(SIMPLE).expect("closed nominally checked helper calls");
    assert!(guest.starts_with(b"\0asm\x01\0\0\0"));
    assert!(guest.len()<16384,"unexpected object or runtime ABI bloat");
    assert_eq!(guest,compile(SIMPLE).unwrap(),"deterministic emitted bytes");
}

#[test]
fn bounded_from_const_emits_wasm_but_runtime_try_from_stays_closed() {
    let valid = r#"
define newtype Port(i64) where value >= 1 and value <= 65535; end
fnc get(Port p): int { return p.to_base(); }
pub fnc main(): int {
    val Port p = Port.from_const(65535);
    return get(p);
}
"#;
    assert!(compile(valid).is_ok());
    let too_low=valid.replace("Port.from_const(65535)","Port.from_const(0)");
    assert_eq!(compile(&too_low).unwrap_err().code,"E_RANGE_INVALID");
    let too_high=valid.replace("Port.from_const(65535)","Port.from_const(65536)");
    assert_eq!(compile(&too_high).unwrap_err().code,"E_RANGE_INVALID");
    let unsafe_runtime=valid.replace("Port.from_const(65535)","Port.try_from(65535)");
    assert!(compile(&unsafe_runtime).is_err(),"unchecked runtime constructor must not compile");
}

#[test]
fn same_i64_repr_never_authorizes_cross_nominal_call() {
    let bad=r#"
define newtype UserId(i64);
define newtype OrderId(i64);
fnc accept(UserId id): int { return id.to_base(); }
pub fnc main(): int {
    val OrderId id = OrderId.from_const(42);
    return accept(id);
}
"#;
    assert_eq!(compile(bad).unwrap_err().code,"E_NEWTYPE_MISMATCH");
}

#[test]
fn raw_nominal_boundaries_and_mutable_values_are_not_executable() {
    for src in [
r#"define newtype UserId(i64); pub fnc main(): int {
  val UserId id = 42; return 0;
}"#,
r#"define newtype UserId(i64); pub fnc main(): int {
  val UserId id = UserId.from_const(42); return id;
}"#,
r#"define newtype UserId(i64); pub fnc main(): int {
  let UserId id = UserId.from_const(42); return 0;
}"#,
r#"define pub newtype UserId(i64); pub fnc main(): int {
  val UserId id = UserId.from_const(42); return 0;
}"#,
r#"define newtype UserId(i64); pub fnc expose(UserId id): int {
  return id.to_base();
} pub fnc main(): int { return 0; }"#,
r#"define newtype UserId(i64); pub fnc main(): int {
  val UserId id = UserId.from_const(42);
  val int leaked = id;
  return leaked;
}"#,
    ] {
        assert!(compile(src).is_err(),"unsupported newtype escape must fail closed: {src}");
    }
}

#[test]
fn imports_and_const_generic_arrays_have_independent_non_executable_gates() {
    let src="define newtype Id(i64); pub fnc main(): int {
       val Id id = Id.from_const(42); val Array<int,4> arr = 0; return id.to_base();
    }";
    assert!(compile(src).is_err(),"fixed Array ABI still unsupported");
    let src="define newtype Id(i64); pub fnc main(): int {
       val Id id = Id.from_const(42); return (id.to_base()) + 1;
    }";
    assert!(compile(src).is_err(),"unverified expression shape cannot be erased");
}
