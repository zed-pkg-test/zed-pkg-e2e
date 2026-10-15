use oreslang_format::{format_source, is_formatted};

#[test]
fn conditional_copy_option_bindings_preserve_const_let_and_guard_operators() {
    let source = r#"fnc choose(Option<int> input) -> int {
if input as const value && value eq 15 then
return value;
else
return 0;
fi
}

fnc change(Option<int> input) -> int {
if (input as let value) eq 15 then
value = value + 1;
return value;
fi
if (input as const value) is type int then
return value;
fi
return 0;
}
"#;
    let expected = r#"fnc choose(Option<int> input) -> int {
  if input as const value && value eq 15 then
    return value;
  else
    return 0;
  fi
}


fnc change(Option<int> input) -> int {
  if (input as let value) eq 15 then
    value = value + 1;
    return value;
  fi
  if (input as const value) is type int then
    return value;
  fi
  return 0;
}
"#;
    let actual = format_source(source).unwrap();
    assert_eq!(actual, expected);
    assert!(is_formatted(expected).unwrap());
    assert_eq!(format_source(&actual).unwrap(), actual);
}

#[test]
fn formatter_does_not_silently_correct_assignment_typo_to_comparison() {
    let source = r#"fnc bad(int x, int y) -> bool {
if x = y then
return true;
fi
return false;
}
"#;
    let actual = format_source(source).unwrap();
    assert!(actual.contains("if x = y then"), "{actual}");
    assert!(!actual.contains("if x eq y then"));
    assert!(!actual.contains("if x == y then"));
}
