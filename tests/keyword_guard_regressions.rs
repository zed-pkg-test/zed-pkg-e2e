use oreslang_format::format_source;

#[test]
fn identifier_functions_named_fi_and_else_remain_calls() {
    let source = "fnc f() {\nif ready then fi(); else(); fi\n}\n";
    let output = format_source(source).unwrap();
    assert!(output.contains("fi();"));
    assert!(output.contains("else();"));
    assert_eq!(format_source(&output).unwrap(), output);
}

#[test]
fn return_continuations_after_blank_lines_are_not_separated() {
    let source = "fnc f() {\nreturn obj\n\n  .method()\n}\n";
    let output = format_source(source).unwrap();
    assert!(!output.contains("return obj;"));
}

#[test]
fn preserves_explicit_conditional_separator() {
    let input = "fnc check() {\nif self.built; then return Err(\"consumed\"); fi\n}\n";
    let output = format_source(input).unwrap();
    assert!(output.contains("if self.built; then"), "{output}");
    assert!(output.contains("return Err(\"consumed\");"), "{output}");
    assert_eq!(format_source(&output).unwrap(), output);
}

#[test]
fn comments_and_blank_lines_do_not_hide_return_continuations() {
    let input = "fnc read() {\nreturn value\n\n// explanation\n  .decode()\n}\n";
    let output = format_source(input).unwrap();
    assert!(!output.contains("return value;"), "{output}");
}

#[test]
fn single_line_return_without_continuation_gets_terminator() {
    let input = "fnc value() {\nreturn compute(1)\n}\n";
    let output = format_source(input).unwrap();
    assert!(output.contains("return compute(1);"), "{output}");
    assert_eq!(format_source(&output).unwrap(), output);
}

#[test]
fn statements_on_later_if_body_lines_are_always_separated() {
    let input = "fnc run() {\nif ready; then\nfirst(); second();\nfi\n}\n";
    let expected = "fnc run() {\n  if ready; then\n    first();\n    second();\n  fi\n}\n";
    let output = format_source(input).unwrap();
    assert_eq!(output, expected);
    assert_eq!(format_source(&output).unwrap(), output);
}

#[test]
fn nested_if_later_line_statements_are_separated() {
    let input = "fnc run() {\nif outer; then\nif inner; then\none(); two();\nfi\nfi\n}\n";
    let expected = "fnc run() {\n  if outer; then\n    if inner; then\n      one();\n      two();\n    fi\n  fi\n}\n";
    let output = format_source(input).unwrap();
    assert_eq!(output, expected);
    assert_eq!(format_source(&output).unwrap(), output);
}
