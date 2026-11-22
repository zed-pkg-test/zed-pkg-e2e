// Focused source-exact Rust compile harness, not the full AttoLang compiler.
// Production lexer, diagnostics and admission module are copied unchanged.
#[path = "attolang-mirror/atto-source.rs/src/diag.rs"]
mod diag;
#[path = "attolang-mirror/atto-source.rs/src/lexer.rs"]
mod lexer;
mod parser {
    // Stub ONLY this external reservation predicate, not executable parser.
    pub fn is_reserved(s: &str) -> bool {
        matches!(s,
            "define" | "class" | "fnc" | "type" | "struct" | "const" |
            "val" | "let" | "pub" | "newtype" | "where" | "int" | "i64")
    }
}
#[path = "attolang-mirror/atto-source.rs/src/newtype_const_generic_admission.rs"]
mod newtype_const_generic_admission;
