use oreslang_format::format_source;

#[test]
fn typed_dispatch_expression_headers_balance_end_and_preserve_type_arguments() {
    let cases = [
        (
            "fnc f(int v): int {\nreturn match<int> v over\non int as item -> {\nreturn 5;\n}\non !int as other -> {\nreturn 4;\n}\nend;\n}\n",
            "return match<int> v over",
        ),
        (
            "fnc f(int v): string {\nreturn switch<string> v over\ncase 5, 4, 3 -> string {\nreturn \"one\";\n}\ndefault -> string {\nreturn \"other\";\n}\nend;\n}\n",
            "return switch<string> v over",
        ),
        (
            "fnc f(Channel<string> ch): string {\nreturn select<string> over\nwhen readch ch: string m -> string {\nreturn m;\n}\nend;\n}\n",
            "return select<string> over",
        ),
        (
            "fnc f(bool flag): int {\nconst int result = match<ret=int> flag over\non true -> {\nreturn 5;\n}\non false -> {\nreturn 4;\n}\nend;\nreturn result;\n}\n",
            "match<ret=int> flag over",
        ),
        (
            "fnc f(int v): int {\ndo match v over\non int as n -> {\nreturn n;\n}\non _ -> {\nreturn 0;\n}\nend\n}\n",
            "do match v over",
        ),
    ];
    for (source, fragment) in cases {
        let formatted = format_source(source).expect("typed dispatch must have a matching end");
        assert!(
            formatted.contains(fragment),
            "lost typed dispatch header: {formatted}"
        );
        assert_eq!(
            format_source(&formatted).unwrap(),
            formatted,
            "not idempotent: {formatted}"
        );
    }
}

#[test]
fn unrelated_member_names_strings_and_non_dispatch_assignments_do_not_open_blocks() {
    let src = "fnc f(): void {\nconst string text = \"return match<int> x over\";\nconst int n = 5;\nstdio.println(text);\nreturn;\n}\n";
    let output = format_source(src).unwrap();
    assert!(output.contains("\"return match<int> x over\""));
    assert_eq!(format_source(&output).unwrap(), output);
}
