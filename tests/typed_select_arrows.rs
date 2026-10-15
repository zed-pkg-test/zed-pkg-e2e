use oreslang_format::{format_source, is_formatted};

#[test]
fn modern_read_projections_keep_the_slim_arrow_and_their_record_braces() {
    let input = r#"do select {
when readch records: let {x, y} -> { process(x, y); }
when readch records: const {foo: string} -> { process(foo); }
when readch tuples: const [int id, ...tail] -> { process(id); }
when readch records: const payload: {foo: string} -> { process(payload.foo); }
}
"#;
    let actual = format_source(input).unwrap();
    for fragment in [
        "when readch records: let {x, y} -> {",
        "when readch records: const {foo: string} -> {",
        "when readch tuples: const [int id, ...tail] -> {",
        "when readch records: const payload: {foo: string} -> {",
    ] {
        assert!(actual.contains(fragment), "missing {fragment}:\n{actual}");
    }
    assert_eq!(format_source(&actual).unwrap(), actual);
    assert!(is_formatted(&actual).unwrap());
}

#[test]
fn modern_write_and_default_arm_arrows_are_not_treated_as_unbraced_bodies() {
    let input = "do select { when writech output, build(1): -> { send(); } default: -> {} }\n";
    let actual = format_source(input).unwrap();
    assert!(
        actual.contains("when writech output, build(1): -> {"),
        "{actual}"
    );
    assert!(actual.contains("default: -> {"), "{actual}");
    assert_eq!(format_source(&actual).unwrap(), actual);
}

#[test]
fn compatibility_read_body_does_not_consume_an_inner_lambda_arrow() {
    let input = "select { case readch channel: val value { call(|x| -> x + 1); } }\n";
    let actual = format_source(input).unwrap();
    assert!(
        actual.contains("when readch channel: val value {"),
        "{actual}"
    );
    assert!(actual.contains("|x| -> x + 1"), "{actual}");
    assert_eq!(format_source(&actual).unwrap(), actual);
}

#[test]
fn a_slim_arrow_must_lead_directly_to_a_braced_body() {
    let err = format_source("do select { when readch channel: int x -> consume(x); }").unwrap_err();
    assert!(err.message().contains("braced arm body"), "{err}");
}

#[test]
fn fat_arrows_and_dangling_typed_read_headers_fail_closed() {
    for invalid in [
        "do select { when readch channel: int n => { consume(n); } }",
        "do select { when readch channel: let {x} => { consume(x); } }",
        "do select { when writech output, 1: => { sent(); } }",
        "do select { default: => {} }",
        "do select { when readch channel: const payload: {x: int} { consume(payload); } }",
        "do select { when readch channel: payload: {x: int} { consume(payload); } }",
    ] {
        assert!(
            format_source(invalid).is_err(),
            "malformed executable arm formatted silently: {invalid}"
        );
    }
}

#[test]
fn legacy_unbraced_body_keeps_nested_expressions() {
    let input = "select { when readch channel: val x process(x ? first : second); default: }\\n";
    let output = format_source(input).unwrap();
    assert!(output.contains("process(x ? first : second);"), "{output}");
    assert_eq!(format_source(&output).unwrap(), output);
}
