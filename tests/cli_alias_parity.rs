//! Both installed commands must be identical views of the same formatter.
use oreslang_format::format_source;
use std::io::Write;
use std::process::{Command, Output, Stdio};

fn run_stdin(binary: &str, args: &[&str], source: &str) -> Output {
    let mut child = Command::new(binary)
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn formatter");
    child
        .stdin
        .take()
        .expect("formatter stdin")
        .write_all(source.as_bytes())
        .expect("write source to stdin");
    child.wait_with_output().expect("wait for formatter")
}

const BINARIES: [&str; 2] = [
    env!("CARGO_BIN_EXE_oresfmt"),
    env!("CARGO_BIN_EXE_oreslang-format"),
];

#[test]
fn both_cli_names_match_the_library_and_reach_a_fixed_point() {
    let fixtures = [
        "fnc answer() => int {\nreturn 42;\n}\n",
        "fnc verify(int value) -> int {\nif value < 0; do return -1; fi\nreturn 2;\n}\n",
        "define class Cache as\n  final Map<int, int> frozen = new Map<int, int>();\nend\n",
    ];

    for source in fixtures {
        let expected = format_source(source).expect("library formats fixture");
        assert_eq!(format_source(&expected).unwrap(), expected);
        for binary in BINARIES {
            let formatted = run_stdin(binary, &["-"], source);
            assert!(
                formatted.status.success(),
                "{binary}: {}",
                String::from_utf8_lossy(&formatted.stderr)
            );
            assert_eq!(
                formatted.stdout,
                expected.as_bytes(),
                "{binary} differs from SDK"
            );

            let check_clean = run_stdin(binary, &["--check", "-"], &expected);
            assert_eq!(
                check_clean.status.code(),
                Some(0),
                "{binary}: canonical check"
            );
            assert!(
                check_clean.stdout.is_empty(),
                "{binary}: check printed source"
            );

            if source != expected {
                let check_dirty = run_stdin(binary, &["--check", "-"], source);
                assert_eq!(check_dirty.status.code(), Some(1), "{binary}: dirty check");
                assert!(
                    check_dirty.stdout.is_empty(),
                    "{binary}: check printed source"
                );
            }
        }
    }
}

#[test]
fn both_cli_names_reject_unbalanced_source_without_partial_output() {
    let invalid = "fnc broken() -> int {\nreturn 1;\n";
    assert!(format_source(invalid).is_err());
    for binary in BINARIES {
        let output = run_stdin(binary, &["-"], invalid);
        assert_eq!(output.status.code(), Some(2), "{binary}");
        assert!(
            output.stdout.is_empty(),
            "{binary}: emitted partial formatted text"
        );
        assert!(!output.stderr.is_empty(), "{binary}: missing diagnostic");
    }
}
