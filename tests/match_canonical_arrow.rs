use oreslang_format::{format_source, is_formatted};

#[test]
fn end_delimited_match_keeps_canonical_on_wildcard() {
    let source = r#"fnc handle(Option<int> x): void {
do match x over
on Some(v) -> { consume(v); }
on _ -> { return; }
end
}
"#;
    let result = format_source(source).unwrap();
    assert!(result.contains("on Some(v) -> {"), "{result}");
    assert!(result.contains("on _ -> {"), "{result}");
    assert!(!result.contains("default ->"), "{result}");
    assert_eq!(format_source(&result).unwrap(), result);
}

#[test]
fn braced_match_does_not_confuse_record_projection_with_arm_body() {
    let source = r#"do match value over {
on Foo as {v} -> { process(v); }
on _ -> { return; }
}
"#;
    let result = format_source(source).unwrap();
    assert!(result.contains("on Foo as {v} -> {"), "{result}");
    assert!(result.contains("on _ -> {"), "{result}");
    assert!(!result.contains("default ->"), "{result}");
    assert_eq!(format_source(&result).unwrap(), result);
    assert!(is_formatted(&result).unwrap());
}

#[test]
fn match_legacy_on_default_migrates_to_on_wildcard_not_switch_default() {
    let source = r#"match value over {
on default -> { return; }
}
"#;
    let result = format_source(source).unwrap();
    assert!(result.contains("on _ -> {"), "{result}");
    assert!(!result.contains("on default"), "{result}");
    assert!(!result.contains("\n  default"), "{result}");
    assert_eq!(format_source(&result).unwrap(), result);
}

#[test]
fn switch_underscore_fallback_still_becomes_default() {
    let source = r#"switch value {
_ -> { return 0; }
case 1 -> { return 1; }
}
"#;
    let result = format_source(source).unwrap();
    assert!(result.contains("default -> {"), "{result}");
    assert_eq!(format_source(&result).unwrap(), result);
}
