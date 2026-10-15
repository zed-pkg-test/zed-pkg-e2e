use oreslang_format::{format_source, is_formatted};

#[test]
fn optional_condition_separators_are_preserved_when_explicit() {
    let source = r#"fnc verify(int value) -> int {
if value < 0; do return -1; fi
if value == 0; then
return 0;
elif value > 0 then
return 1;
fi
return 2;
}
"#;
    let expected = r#"fnc verify(int value) -> int {
  if value < 0; then
    return -1;
  fi
  if value == 0; then
    return 0;
  elif value > 0 then
    return 1;
  fi
  return 2;
}
"#;
    let actual = format_source(source).unwrap();
    assert_eq!(actual, expected);
    assert!(is_formatted(expected).unwrap());
    assert_eq!(format_source(&actual).unwrap(), actual);
}

#[test]
fn condition_separator_whitespace_is_canonical() {
    let source = "fnc f() -> int {\nif ready   ;  then return 5; fi\n}\n";
    let expected = "fnc f() -> int {\n  if ready; then\n    return 5;\n  fi\n}\n";
    let actual = format_source(source).unwrap();
    assert_eq!(actual, expected);
}

#[test]
fn final_field_spelling_is_preserved_until_compiler_admission() {
    let source = r#"define class Cache as
  final Map<int, int> frozen = new Map<int, int>();
  final mut Map<int, int> writable = new Map<int, int>();
end
"#;
    assert_eq!(format_source(source).unwrap(), source);
}
