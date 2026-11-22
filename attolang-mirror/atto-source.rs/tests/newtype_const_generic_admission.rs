//! Real compiler entry-point admission tests. Positive syntax is still gated:
//! no Wasm guest can be produced until a type-preserving nominal/layout backend.

use atto_compiler::{compile, lexer::lex, newtype_const_generic_admission::{inspect, Interval}};

const PORT: &str = "define newtype Port(i64) where value gte 1 and value lte 65535; end\n";
const NOMINAL: &str = "define newtype UserId(i64); define newtype OrderId(i64);\n";

fn expect_diagnostic(source: &str, diagnostic: &str) {
    let err = compile(source).expect_err("unsupported or unsafe type must never emit Wasm");
    assert_eq!(err.code, diagnostic, "source {source}: {err}");
    assert!(err.pos.line > 0 && err.pos.col > 0);
}

#[test]
fn intervals_accept_both_endpoints_and_remain_unlowered() {
    let source = format!("{PORT} pub fnc main(): int {{
        val Port p1 = Port.from_const(1);
        val Port p2 = Port.from_const(65535);
        return 42;
    }}");
    let proof = inspect(&lex(&source).unwrap()).unwrap();
    assert_eq!(proof.nominal["Port"], Interval {low: 1, high: 65535});
    assert!(proof.requires_backend);
    expect_diagnostic(&source, "E_UNSUPPORTED");
}

#[test]
fn range_violations_and_contradictions_have_stable_diagnostics() {
    expect_diagnostic(&format!("{PORT} pub fnc main(): int {{
        val Port bad = Port.from_const(0); return 42;
    }}"), "E_RANGE_INVALID");
    expect_diagnostic(&format!("{PORT} pub fnc main(): int {{
        val Port bad = Port.from_const(65536); return 42;
    }}"), "E_RANGE_INVALID");
    expect_diagnostic("define newtype Broken(i64) where value gte 10 and value lte 9; end",
        "E_RANGE_INVALID");
    expect_diagnostic("define newtype Min(i64) where value gte -9223372036854775808 and value lte -9223372036854775808; end
        pub fnc main(): int { return 42; }", "E_UNSUPPORTED");
    expect_diagnostic("define newtype Invalid(i64) where value gte 1 or value lte 10; end",
        "E_RANGE_UNPROVEN");
    expect_diagnostic("define newtype Invalid(i64) where is_prime(value); end",
        "E_RANGE_UNPROVEN");
    expect_diagnostic("define newtype Invalid(i64) where value ge 1; end",
        "E_RANGE_UNPROVEN");
}

#[test]
fn nominal_mismatch_and_implicit_raw_construction_do_not_emit_guest() {
    expect_diagnostic(&format!("{NOMINAL} fnc accept_user(UserId id) -> int {{ return 42; }}
        pub fnc main(): int {{
          val OrderId order = OrderId.from_const(7);
          return accept_user(order);
        }}"), "E_NEWTYPE_MISMATCH");
    expect_diagnostic(&format!("{NOMINAL} pub fnc main(): int {{
        val UserId wrong = OrderId.from_const(7);
        return 42;
    }}"), "E_NEWTYPE_MISMATCH");
    expect_diagnostic(&format!("{NOMINAL} pub fnc main(): int {{
        val UserId wrong = 7;
        return 42;
    }}"), "E_NEWTYPE_CONSTRUCTION");
    expect_diagnostic("define newtype UserId(i64); define newtype UserId(i64);",
        "E_DUPLICATE");
}

#[test]
fn fixed_array_dimension_proof_uses_checked_arithmetic() {
    let source = "pub fnc main(): int { val Array<int, (2 + 2) * 4> x = 0; return 42; }";
    let proof = inspect(&lex(source).unwrap()).unwrap();
    assert_eq!(proof.known_lengths, vec![16]);
    expect_diagnostic(source, "E_UNSUPPORTED");
    expect_diagnostic("pub fnc main(): int { val Array<int, -1> x = 0; return 42; }",
        "E_CONST_GENERIC_ARG");
    expect_diagnostic("pub fnc main(): int { val Array<int, runtime_n> x = 0; return 42; }",
        "E_CONST_GENERIC_ARG");
    expect_diagnostic("pub fnc main(): int { val Array<int, 9223372036854775807 * 3> x = 0; return 42; }",
        "E_CONST_OVERFLOW");
    expect_diagnostic("pub fnc main(): int { val Array<int, 1025 * 1024> x = 0; return 42; }",
        "E_CONST_LAYOUT_BUDGET");
    expect_diagnostic("pub fnc main(): int { val Array<int, (4 + 1> x = 0; return 42; }",
        "E_CONST_GENERIC_ARG");
}

#[test]
fn symbolic_matrix_parameters_are_recognized_without_unsafe_specialization() {
    let source = r#"
        define struct Matrix<T, const usize R, const usize C> as
          data: Array<T, R * C>;
        end
        pub fnc main(): int { return 42; }
    "#;
    let proof = inspect(&lex(source).unwrap()).unwrap();
    assert!(proof.requires_backend);
    assert!(proof.known_lengths.is_empty());
    expect_diagnostic(source, "E_UNSUPPORTED");
    expect_diagnostic("define struct M<T, const usize R, const usize R> as end",
        "E_DUPLICATE");
}

#[test]
fn ordinary_source_and comment_or_string_lookalikes_are_unchanged() {
    let source = r#"
        // define newtype Fake(i64) where value gte 1; end
        pub fnc main(): int {
          stdio.println("Array<int, 0> define newtype Ghost(i64)");
          return 42;
        }
    "#;
    let proof = inspect(&lex(source).unwrap()).unwrap();
    assert!(proof.nominal.is_empty());
    assert!(!proof.requires_backend);
    let guest = compile(source).unwrap();
    assert!(guest.starts_with(b"\0asm\x01\0\0\0"));
}

#[test]
fn unsafe_nominal_constructors_reject_without_a_guest_artifact() {
    for source in [
        "define newtype Port(i64); pub fnc main(): int { val Port p = Port.from_const(3); return 42; }",
        "define pub newtype Port(i64); pub fnc main(): int { return 42; }",
        "define newtype Seconds(int); pub fnc main(): int { return 42; }",
        "define newtype Port(i64); pub fnc main(): int { val Port p = Port.try_from(8080); return 42; }",
    ] {
        expect_diagnostic(source, "E_UNSUPPORTED");
    }
}

#[test]
fn proof_is_bounded_and explicit_source_is_validated_before_emission() {
    let many = format!("{} pub fnc main(): int {{ return 42; }}",
        (0..70).map(|i| format!("define newtype T{i}(i64);")).collect::<Vec<_>>().join("\n"));
    expect_diagnostic(&many, "E_SOURCE_LIMIT");
    expect_diagnostic("define newtype P(u16); pub fnc main(): int { return 42; }",
        "E_UNSUPPORTED_TYPE");
}
