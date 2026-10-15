//! Deterministic grammar-constrained property checks.
//! No network crates or nondeterministic OS randomness; every failure reports a seed.
use oreslang_format::format_source;

#[derive(Clone, Copy)]
struct Rng(u64);

impl Rng {
    fn next(&mut self) -> u64 {
        // 64-bit LCG: repeatable on every Rust target, including MSRV 1.85.
        self.0 = self
            .0
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        self.0
    }

    fn choose(&mut self, size: usize) -> usize {
        (self.next() % size as u64) as usize
    }
}

// Test a subset whose semicolons and canonical signatures are already explicit.
// Newlines/indentation may change; significant source characters must not.
fn significant(source: &str) -> String {
    let mut output = String::new();
    let mut quoted: Option<char> = None;
    let mut escaped = false;
    for ch in source.chars() {
        if let Some(quote) = quoted {
            output.push(ch);
            if escaped {
                escaped = false;
            } else if ch == '\\' {
                escaped = true;
            } else if ch == quote {
                quoted = None;
            }
        } else if matches!(ch, '"' | '\'') {
            quoted = Some(ch);
            output.push(ch);
        } else if !ch.is_whitespace() {
            output.push(ch);
        }
    }
    output
}

fn case(seed: u64) -> String {
    let mut rng = Rng(seed);
    let value = rng.choose(10000);
    let other = rng.choose(10000);
    let gap = [" ", "  ", "\t", "\n"][rng.choose(4)];
    let inner = if rng.choose(2) == 0 {
        format!("if nested; then return {value}; else return {other}; fi")
    } else {
        "if nested; then first(); second(); fi".to_string()
    };
    format!("fnc generated() -> int {{\nif ready; then{gap}{inner}\nfi\nreturn {value};\n}}\n")
}

#[test]
fn seeded_conditional_cases_preserve_structure_and_reach_fixed_point() {
    for seed in 0..512_u64 {
        let input = case(seed);
        let formatted = format_source(&input).unwrap_or_else(|error| {
            panic!("seed {seed}: initial format failed: {error:?}\n{input}")
        });
        let twice = format_source(&formatted).unwrap_or_else(|error| {
            panic!("seed {seed}: second format failed: {error:?}\n{formatted}")
        });
        assert_eq!(twice, formatted, "seed {seed}: not idempotent");
        assert_eq!(
            significant(&input),
            significant(&formatted),
            "seed {seed}: formatter modified non-whitespace syntax\ninput:\n{input}\nformatted:\n{formatted}"
        );
    }
}

#[test]
fn quoted_keywords_survive_keyword_expansion() {
    for seed in 0..128_u64 {
        let mut rng = Rng(seed);
        let value = rng.choose(100000);
        let literal = format!("text if; then else fi value={value}");
        let input = format!(
            "fnc f() -> String {{\nif ready; then return \"{literal}\"; else return \"other\"; fi\n}}\n"
        );
        let output = format_source(&input).unwrap();
        assert!(output.contains(&format!("\"{literal}\"")), "seed {seed}");
        assert_eq!(format_source(&output).unwrap(), output, "seed {seed}");
        assert_eq!(significant(&input), significant(&output), "seed {seed}");
    }
}

#[test]
fn guards_continued_returns_with_skipped_trivia() {
    for separator in ["\n", "\n\n", "\n// note\n", "\n\n// note\n"] {
        let input = format!("fnc f() -> int {{\nreturn item{separator}  .value()\n}}\n");
        let formatted = format_source(&input).unwrap();
        assert!(
            !formatted.contains("return item;"),
            "{separator:?}: {formatted}"
        );
        assert_eq!(format_source(&formatted).unwrap(), formatted);
    }
}
