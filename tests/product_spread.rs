use oreslang_format::{format_source, is_formatted};

#[test]
fn product_spread_remains_explicit_and_record_field_order_is_preserved() {
    let src = r#"pub fnc foo(int a, bool b, string c) => void {
return;
}

pub routine main() => void {
foo(...(a: 5, c: "hi", b: true));
foo(...(5, true, "hi"));
foo((a: 5, b: true, c: "hi"));
foo((5, true, "hi"));
return;
}
"#;
    let expected = r#"pub fnc foo(int a, bool b, string c) -> void {
  return;
}


pub routine main() -> void {
  foo(...(a: 5, c: "hi", b: true));
  foo(...(5, true, "hi"));
  foo((a: 5, b: true, c: "hi"));
  foo((5, true, "hi"));
  return;
}
"#;
    let result = format_source(src).unwrap();
    assert_eq!(result, expected);
    assert!(is_formatted(&result).unwrap());
    assert_eq!(format_source(&result).unwrap(), result);
}

#[test]
fn product_spread_keeps_source_order_and_nested_expressions_intact() {
    let src = r#"fnc call() -> void {
foo(...(c: side_effect(1), a: side_effect(2), b: side_effect(3)));
foo(...(build_a(), build_b(), build_c()));
return;
}
"#;
    let output = format_source(src).unwrap();
    assert!(output.contains("foo(...(c: side_effect(1), a: side_effect(2), b: side_effect(3)));"));
    assert!(output.contains("foo(...(build_a(), build_b(), build_c()));"));
    assert_eq!(format_source(&output).unwrap(), output);
}
