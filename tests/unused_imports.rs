use oreslang_format::{format_source, is_formatted};

fn canonical(source: &str) -> String {
    let once = format_source(source).unwrap();
    assert_eq!(
        format_source(&once).unwrap(),
        once,
        "formatting must be idempotent"
    );
    assert!(is_formatted(&once).unwrap());
    once
}

#[test]
fn comments_unused_imports_but_preserves_their_source_text() {
    let src = r#"// oresfmt: pure-import
import class Box from "./box";
import fnc build from "./factory"; // the original trailing comment
pub fnc main(): void {
  build();
  return;
}
"#;
    let formatted = canonical(src);
    assert!(formatted.contains("// import class Box from \"./box\";\n"));
    assert!(
        formatted.contains("import fnc build from \"./factory\"; // the original trailing comment")
    );
    assert!(!formatted.contains("// import fnc build"));
}

#[test]
fn comments_aliases_by_local_name_not_original_name() {
    let src = r#"// oresfmt: pure-import
import class ArrayList as JList from "java:java.util.ArrayList";
import fnc abs as jabs from "java:java.lang.Math";
// oresfmt: pure-import
import * as utilities from "./util";
// oresfmt: pure-import
import module * as services from "./services";
pub fnc main(): int {
  return jabs(-4);
}
"#;
    let formatted = canonical(src);
    assert!(formatted.contains("// import class ArrayList as JList"));
    assert!(formatted.contains("import fnc abs as jabs from"));
    assert!(formatted.contains("// import * as utilities"));
    assert!(formatted.contains("// import module * as services"));
}

#[test]
fn grouped_imports_remain_active_when_any_member_is_referenced() {
    let src = r#"import types (One, Two, Three) from "./types";
import fnc {run, stop} from "./other";
// oresfmt: pure-import
import types A, B, C from "./more";
pub fnc main(): void {
  val One x;
  stop();
  return;
}
"#;
    let formatted = canonical(src);
    assert!(formatted.contains("import types (One, Two, Three)"));
    assert!(formatted.contains("import fnc {run, stop}"));
    assert!(formatted.contains("// import types A, B, C"));
}

#[test]
fn ignores_appearances_in_comments_strings_paths_and_symbols() {
    let src = r#"// oresfmt: pure-import
import class Phantom from "./Phantom";
// oresfmt: pure-import
import type Hidden from "./Hidden";
pub fnc main(): void {
  // Phantom and Hidden are mentioned, but not referenced.
  val name = "Phantom Hidden";
  val symbol = #Hidden;
  return;
}
"#;
    let formatted = canonical(src);
    assert!(formatted.contains("// import class Phantom from"));
    assert!(formatted.contains("// import type Hidden from"));
}

#[test]
fn intentional_side_effect_import_is_kept_with_marker() {
    let src = r#"// oresfmt: keep-import
import module Bootstrap from "./boot";
import module NotNeeded from "./extra"; // oresfmt: keep-import
pub fnc main(): void {
  return;
}
"#;
    let formatted = canonical(src);
    assert!(formatted.contains("\nimport module Bootstrap from"));
    assert!(formatted.contains("\nimport module NotNeeded from"));
    assert!(!formatted.contains("// import module"));
}

#[test]
fn namespace_header_and_multiline_imports_are_supported() {
    let src = r#"namespace app;
// oresfmt: pure-import
import types (
  One,
  Two
) from "./types";
pub fnc main(): void {
  return;
}
"#;
    let formatted = canonical(src);
    assert!(formatted.contains("// import types ("));
    assert!(formatted.contains("// One,"));
    assert!(formatted.contains("// Two"));
    assert!(formatted.contains("// ) from \"./types\";"));
    assert!(formatted.contains("namespace app;"));
}

#[test]
fn code_sharing_import_line_is_never_swallowed_by_comment() {
    let src = "namespace app; import class X from './x';\n";
    let formatted = canonical(src);
    assert_eq!(formatted, src);
}

#[test]
fn a_lexically_used_import_stays_active_in_nested_code() {
    let src = r#"import actor Worker from "./worker";
define actor Supervisor as
  pub start(): void {
    val task = spawn Worker();
    return;
  }
end
"#;
    let formatted = canonical(src);
    assert!(formatted.starts_with("import actor Worker"));
    assert!(!formatted.contains("// import actor Worker"));
}

#[test]
fn keep_marker_applies_to_exactly_one_import() {
    let src = r#"// oresfmt: pure-import
import class First from "./one"; // oresfmt: keep-import
// oresfmt: pure-import
import class Second from "./two";
// oresfmt: pure-import
import class Third from "./three"; // discussion about keep-import
// oresfmt: keep-import
import class Fourth from "./four";
// oresfmt: pure-import
import class Fifth from "./five";
pub fnc main(): void {
  return;
}
"#;
    let formatted = canonical(src);
    assert!(formatted.contains("\nimport class First"));
    assert!(formatted.contains("// import class Second from"));
    assert!(formatted.contains("// import class Third from"));
    assert!(formatted.contains("\nimport class Fourth from"));
    assert!(formatted.contains("// import class Fifth from"));
}

#[test]
fn unrelated_prose_and_blank_lines_do_not_activate_keep_marker() {
    let src = r#"// Discussion: oresfmt: keep-import
// oresfmt: pure-import
import class Old from "./old";

// oresfmt: keep-import

// oresfmt: pure-import
import class Other from "./other";
pub fnc main(): void {
  return;
}
"#;
    let formatted = canonical(src);
    assert!(formatted.contains("// import class Old from"));
    assert!(formatted.contains("// import class Other from"));
}

#[test]
fn quoted_slashes_and_unicode_source_offsets_are_safe() {
    let src = r#"// café 雪 🎉
// oresfmt: pure-import
import class Éclair from "https://example.invalid/oresfmt: keep-import";
import class Météo from "./weather";
pub fnc main(): void {
  Météo();
  return;
}
"#;
    let formatted = canonical(src);
    assert!(
        formatted.contains(
            "// import class Éclair from \"https://example.invalid/oresfmt: keep-import\";"
        )
    );
    assert!(formatted.contains("\nimport class Météo from"));
}

#[test]
fn inline_template_name_ambiguity_keeps_imports_active() {
    let src = "import class Widget from './widget';\nfnc main() -> void {\n  val template = \u{60}Widget\u{60};\n  return;\n}\n";
    let formatted = canonical(src);
    assert!(formatted.starts_with("import class Widget"));
}

#[test]
fn unknown_import_grammar_does_not_partially_comment_a_header() {
    let src = r#"import class First from "./one";
import feature NewSyntax from "./other";
pub fnc main(): void {
  return;
}
"#;
    let formatted = canonical(src);
    assert!(formatted.starts_with("import class First"));
    assert!(formatted.contains("\nimport feature NewSyntax from"));
}

#[test]
fn standalone_only_prevents_commenting_imports_with_other_statements() {
    let src = r#"import class Foo from "./one"; import class Bar from "./two";
pub fnc main(): void {
  return;
}
"#;
    let formatted = canonical(src);
    assert!(formatted.starts_with("import class Foo from"));
    assert!(formatted.contains("import class Bar from"));
    assert!(!formatted.contains("// import class"));
}

#[test]
fn large_header_with_many_imports_is_deterministic() {
    let mut src = String::new();
    for i in 0..512 {
        src.push_str(&format!(
            "// oresfmt: pure-import\nimport class Imported{i} from \"./deps/{i}\";\n"
        ));
    }
    src.push_str("pub fnc main(): void {\n  Imported511();\n  return;\n}\n");
    let formatted = canonical(&src);
    assert!(formatted.contains("// import class Imported0 from"));
    assert!(formatted.contains("// import class Imported510 from"));
    assert!(formatted.contains("\nimport class Imported511 from"));
    assert_eq!(formatted.matches("// import class ").count(), 511);
}

#[test]
fn default_preserves_unused_source_imports_that_may_initialize_modules() {
    let src = r#"import fnc unused_fn from "./initializes";
import class Other from "./might-init";
import module Bootstrap from "./boot";
pub routine main(): void {
  return;
}
"#;
    let formatted = canonical(src);
    assert!(formatted.starts_with("import fnc unused_fn"));
    assert!(formatted.contains("\nimport class Other from"));
    assert!(formatted.contains("\nimport module Bootstrap from"));
    assert!(!formatted.contains("// import"));
}

#[test]
fn keep_import_wins_over_explicit_purity_for_initialization() {
    let src = r#"// oresfmt: pure-import
import module Init from "./init"; // oresfmt: keep-import
pub fnc main(): void {
  return;
}
"#;
    let formatted = canonical(src);
    assert!(formatted.contains("\nimport module Init from"));
    assert!(!formatted.contains("// import module Init"));
}

#[test]
fn pure_marker_in_unrelated_prose_does_not_comment_an_import() {
    let src = r#"// Discussion: oresfmt: pure-import
import class Keep from "./keep";
pub fnc main(): void {
  return;
}
"#;
    let formatted = canonical(src);
    assert!(formatted.contains("\nimport class Keep from"));
}

#[test]
fn exact_trailing_pure_marker_allows_commenting_an_unused_import() {
    let src = r#"import class Reviewed from "./reviewed"; // oresfmt: pure-import
import class Unreviewed from "./unknown";
pub fnc main(): void {
  return;
}
"#;
    let formatted = canonical(src);
    assert!(formatted.contains("// import class Reviewed from"));
    assert!(formatted.contains("\nimport class Unreviewed from"));
}

#[test]
fn import_in_cyclic_dependency_remains_active_without_purity_assertion() {
    // Java ImportGraph initializationGroups includes this edge even if
    // only_for_init is never referenced in this file.
    let src = r#"import fnc only_for_init from "./cyclic.ores";
pub fnc init(): void {
  return;
}
pub routine main(): void {
  return;
}
"#;
    let formatted = canonical(src);
    assert!(formatted.contains("import fnc only_for_init from \"./cyclic.ores\";"));
    assert!(!formatted.contains("// import fnc only_for_init"));
}

#[test]
fn selector_level_marker_cannot_deactivate_multiline_import() {
    let src = r#"import types (
  First, // oresfmt: pure-import
  Second
) from "./potentially-effectful";
pub fnc main(): void {
  return;
}
"#;
    let formatted = canonical(src);
    assert!(formatted.contains("import types ("));
    assert!(formatted.contains(") from \"./potentially-effectful\";"));
    assert!(!formatted.contains("// import types ("));
}

#[test]
fn trailing_pure_marker_on_final_line_deactivates_reviewed_multiline_import() {
    let src = r#"import types (
  First,
  Second
) from "./confirmed-pure"; // oresfmt: pure-import
pub fnc main(): void {
  return;
}
"#;
    let formatted = canonical(src);
    assert!(formatted.contains("// import types ("));
    assert!(formatted.contains("// ) from \"./confirmed-pure\";"));
}

#[test]
fn selector_level_keep_marker_does_not_override_final_line_purity() {
    let src = r#"import types (
  First, // oresfmt: keep-import
  Second
) from "./confirmed-pure"; // oresfmt: pure-import
pub fnc main(): void {
  return;
}
"#;
    let formatted = canonical(src);
    assert!(formatted.contains("// import types ("));
}
