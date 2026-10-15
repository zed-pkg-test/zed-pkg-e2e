use oreslang_format::{format_source, is_formatted};

#[test]
fn generic_dependent_defaults_named_calls_and_holes_roundtrip() {
    let source = r#"fnc choose<A, B=int, C=B, D=Array<C>>(A input): int {
  return 7;
}
fnc use(): int {
  val int a = choose<A=int, D=Array<string>>(7);
  val int b = choose<C=int, A=int>(7);
  val int c = choose<int, _>(7);
  return a + b + c;
}
"#;
    let formatted = format_source(source).expect("generic source should format");
    for fragment in [
        "choose<A, B=int, C=B, D=Array<C>>",
        "choose<A=int, D=Array<string>>(7)",
        "choose<C=int, A=int>(7)",
        "choose<int, _>(7)",
    ] {
        assert!(formatted.contains(fragment), "lost {fragment}: {formatted}");
    }
    assert_eq!(format_source(&formatted).unwrap(), formatted);
    assert!(is_formatted(&formatted).unwrap());
}

#[test]
fn unsupported_generic_syntax_is_not_silently_rewritten() {
    let source = r#"fnc choose<T=int>(): int {
  return 42;
}
fnc fail(): int {
  val int nested = choose<T=Option<Array<_>>>();
  val int explicit_hole = choose<T=_>();
  return nested + explicit_hole;
}
"#;
    let once = format_source(source).expect("formatting preserves source before type validation");
    assert!(once.contains("choose<T=Option<Array<_>>>()"), "{once}");
    assert!(once.contains("choose<T=_>()"), "{once}");
    assert_eq!(format_source(&once).unwrap(), once);
}
