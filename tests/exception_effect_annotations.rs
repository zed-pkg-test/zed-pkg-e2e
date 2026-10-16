use oreslang_format::format_source;

// Proposed postfix exceptional-effect syntax must roundtrip without inventing,
// removing, or conflating throw/raise/panic contracts. This is a formatter
// preservation gate, NOT a claim that a compiler executes the syntax yet.
#[test]
fn preserves_optional_effect_annotations_for_fncs_and_methods() {
    let source = r#"pub fnc fetch(id: int) -> User throws(NetworkError, DecodeError) raises(AccessError) panics(InvariantError) {
  throw new NetworkError();
}

define class Worker as {
  method handle() -> void raises(Error) {
    raise new Error();
  }
  static fnc shutdown() -> void panics(Error) {
    panic new Error();
  }
}
"#;
    let formatted =
        format_source(source).expect("formatter must preserve proposed effect declarations");
    assert!(formatted.contains("throws(NetworkError, DecodeError)"));
    assert!(formatted.contains("raises(AccessError)"));
    assert!(formatted.contains("panics(InvariantError)"));
    assert!(formatted.contains("method handle() -> void raises(Error)"));
    assert!(formatted.contains("static fnc shutdown() -> void panics(Error)"));
    assert!(formatted.contains("raise new Error();"));
    assert!(formatted.contains("panic new Error();"));
    assert_eq!(format_source(&formatted).unwrap(), formatted);
}

#[test]
fn keeps_exception_words_inside_literals_opaque() {
    let source =
        "fnc f() -> String {\n  return \"throws(Error) raises(Error) panics(Error)\";\n}\n";
    let formatted = format_source(source).unwrap();
    assert!(formatted.contains("\"throws(Error) raises(Error) panics(Error)\""));
    assert_eq!(format_source(&formatted).unwrap(), formatted);
}

#[test]
fn preserves_bare_clauses_generics_and_explicit_mutable_receiver() {
    let source = "fnc load<T>(value: T) -> T throws raises panics {\n  return value;\n}\n\ndefine class Worker as {\n  pub handle(mut self)() -> void raises(Error) {\n    raise new Error();\n  }\n}\n";
    let formatted = format_source(source).unwrap();
    assert!(formatted.contains("throws raises panics"));
    assert!(formatted.contains("handle(mut self)() -> void raises(Error)"));
    assert_eq!(format_source(&formatted).unwrap(), formatted);
}

#[test]
fn preserves_named_pipe_and_interface_effect_signatures() {
    let source = "fnc load = |int id| -> void throws(Error) {\n  throw new Error();\n}\n\ndefine interface Loader as\n  load(id: int) => User throws(Error) raises;\nend\n";
    let formatted = format_source(source).unwrap();
    assert!(formatted.contains("fnc load = |int id| -> void throws(Error)"));
    assert!(formatted.contains("=> User throws(Error) raises;"));
    assert_eq!(format_source(&formatted).unwrap(), formatted);
}

#[test]
fn preserves_supplied_clause_order_and_never_invents_annotations() {
    let source = "fnc f() -> void panics(Error) raises(Error) throws(Error) {\n  panic new Error();\n}\n\nfnc g() -> void {\n  raise new Error();\n}\n";
    let formatted = format_source(source).unwrap();
    assert!(formatted.contains("panics(Error) raises(Error) throws(Error)"));
    assert!(formatted.contains("fnc g() -> void {"));
    assert_eq!(format_source(&formatted).unwrap(), formatted);
}
