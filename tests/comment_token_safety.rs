//! Keyword-shaped text inside comments and literals must stay opaque.
use oreslang_format::format_source;

#[test]
fn line_comments_preserve_all_interior_spacing_and_punctuation() {
    for seed in 0..128_u32 {
        let marker = format!("//  fi();  else();  then;  {seed}  keyword  ");
        let source = format!(
            "fnc read() -> int {{\nif ready; then\n{marker}\nreturn 1;\nelse\nreturn 2;\nfi\n}}\n"
        );
        let result = format_source(&source).unwrap();
        assert!(result.contains(&marker), "seed {seed}: {result}");
        assert_eq!(format_source(&result).unwrap(), result, "seed {seed}");
    }
}

#[test]
fn block_comments_preserve_nested_keyword_shaped_text() {
    let payload = "/*  fi(); else() then;  \n   else; return 404; fi  */";
    let source = format!(
        "fnc read() -> int {{\n{payload}\nif ready; then return 1; else return 2; fi\n}}\n"
    );
    let result = format_source(&source).unwrap();
    assert!(result.contains(payload), "{result}");
    assert_eq!(format_source(&result).unwrap(), result);
}
