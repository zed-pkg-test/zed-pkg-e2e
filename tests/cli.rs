#[cfg(unix)]
use std::ffi::OsStr;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::time::{SystemTime, UNIX_EPOCH};

struct TempRepo {
    root: PathBuf,
}

impl TempRepo {
    fn new(label: &str) -> Self {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root =
            std::env::temp_dir().join(format!("oresfmt-{label}-{}-{nonce}", std::process::id()));
        fs::create_dir_all(&root).unwrap();
        git(&root, &["init", "-q"]);
        git(&root, &["config", "user.name", "oresfmt-tests"]);
        git(
            &root,
            &["config", "user.email", "oresfmt-tests@example.invalid"],
        );
        Self { root }
    }

    fn write(&self, name: &str, content: &str) -> PathBuf {
        let path = self.root.join(name);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(&path, content).unwrap();
        path
    }

    fn commit_all(&self, message: &str) {
        git(&self.root, &["add", "."]);
        git(&self.root, &["commit", "-qm", message]);
    }

    fn run(&self, args: &[&str]) -> Output {
        Command::new(env!("CARGO_BIN_EXE_oresfmt"))
            .current_dir(&self.root)
            .args(args)
            .output()
            .unwrap()
    }

    #[cfg(unix)]
    fn run_os(&self, args: &[&OsStr]) -> Output {
        Command::new(env!("CARGO_BIN_EXE_oresfmt"))
            .current_dir(&self.root)
            .args(args)
            .output()
            .unwrap()
    }
}

impl Drop for TempRepo {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

fn git(root: &Path, args: &[&str]) {
    let output = Command::new("git")
        .current_dir(root)
        .args(args)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "git {:?} failed: {}",
        args,
        String::from_utf8_lossy(&output.stderr)
    );
}

const UNFORMATTED: &str = "fnc answer() => int {\nreturn 42;\n}\n";
const FORMATTED: &str = "fnc answer() -> int {\n  return 42;\n}\n";

#[test]
fn dry_run_is_default_and_never_writes() {
    let repo = TempRepo::new("dry-run");
    let path = repo.write("sample.ores", UNFORMATTED);

    let output = repo.run(&["sample.ores"]);

    assert!(output.status.success());
    assert_eq!(fs::read_to_string(path).unwrap(), UNFORMATTED);
    assert!(String::from_utf8_lossy(&output.stdout).contains("would format sample.ores"));
}

#[test]
fn check_is_nonzero_but_does_not_write() {
    let repo = TempRepo::new("check");
    let path = repo.write("sample.ores", UNFORMATTED);

    let output = repo.run(&["--check", "sample.ores"]);

    assert_eq!(output.status.code(), Some(1));
    assert_eq!(fs::read_to_string(path).unwrap(), UNFORMATTED);
    assert!(String::from_utf8_lossy(&output.stderr).contains("needs formatting"));
}

#[test]
fn write_allows_clean_tracked_file() {
    let repo = TempRepo::new("clean-tracked");
    let path = repo.write("sample.ores", UNFORMATTED);
    repo.commit_all("baseline");

    let output = repo.run(&["--write", "sample.ores"]);

    assert!(output.status.success());
    assert_eq!(fs::read_to_string(path).unwrap(), FORMATTED);
}

#[test]
fn unused_import_check_dry_run_and_write_are_consistent() {
    let repo = TempRepo::new("unused-import-cli");
    let source = "// oresfmt: pure-import\nimport class Old from \"./old\";\nimport class Used from \"./used\";\npub fnc main(): void {\n  Used();\n  return;\n}\n";
    let target = repo.write("example.ores", source);
    repo.commit_all("baseline");

    let dry = repo.run(&["example.ores"]);
    assert!(dry.status.success());
    assert_eq!(fs::read_to_string(&target).unwrap(), source);
    assert!(String::from_utf8_lossy(&dry.stdout).contains("would format example.ores"));

    let check = repo.run(&["--check", "example.ores"]);
    assert_eq!(check.status.code(), Some(1));
    assert_eq!(fs::read_to_string(&target).unwrap(), source);

    let write = repo.run(&["--write", "example.ores"]);
    assert!(write.status.success());
    let formatted = fs::read_to_string(&target).unwrap();
    assert!(formatted.contains("\n// import class Old from"));
    assert!(formatted.contains("\nimport class Used from"));
    assert!(formatted.contains("  Used();"));
    assert!(repo.run(&["--check", "example.ores"]).status.success());
}

#[test]
fn write_refuses_dirty_tracked_file_by_default() {
    let repo = TempRepo::new("dirty-tracked");
    let path = repo.write("sample.ores", UNFORMATTED);
    repo.commit_all("baseline");
    repo.write("sample.ores", "fnc answer() => int {\nreturn 43;\n}\n");
    let before = fs::read_to_string(&path).unwrap();

    let output = repo.run(&["--write", "sample.ores"]);

    assert_eq!(output.status.code(), Some(2));
    assert_eq!(fs::read_to_string(path).unwrap(), before);
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("refusing to modify dirty tracked file")
    );
}

#[test]
fn dirty_tracked_override_is_explicit() {
    let repo = TempRepo::new("dirty-override");
    let path = repo.write("sample.ores", UNFORMATTED);
    repo.commit_all("baseline");
    repo.write("sample.ores", "fnc answer() => int {\nreturn 43;\n}\n");

    let output = repo.run(&["--write", "--ok-to-mod-dirty-files", "sample.ores"]);

    assert!(output.status.success());
    assert_eq!(
        fs::read_to_string(path).unwrap(),
        "fnc answer() -> int {\n  return 43;\n}\n"
    );
}

#[test]
fn write_refuses_untracked_file_unless_explicitly_allowed() {
    let repo = TempRepo::new("untracked");
    let path = repo.write("sample.ores", UNFORMATTED);

    let refused = repo.run(&["--write", "sample.ores"]);
    assert_eq!(refused.status.code(), Some(2));
    assert_eq!(fs::read_to_string(&path).unwrap(), UNFORMATTED);
    assert!(
        String::from_utf8_lossy(&refused.stderr)
            .contains("refusing to modify untracked or ignored file")
    );

    let allowed = repo.run(&["--write", "--ok-to-mod-untracked-files", "sample.ores"]);
    assert!(allowed.status.success());
    assert_eq!(fs::read_to_string(path).unwrap(), FORMATTED);
}

#[test]
fn write_preflights_every_change_before_touching_any_file() {
    let repo = TempRepo::new("atomic-preflight");
    let tracked = repo.write("a.ores", UNFORMATTED);
    repo.commit_all("tracked baseline");
    let untracked = repo.write("b.ores", UNFORMATTED);

    let output = repo.run(&["--write", "."]);

    assert_eq!(output.status.code(), Some(2));
    assert_eq!(fs::read_to_string(tracked).unwrap(), UNFORMATTED);
    assert_eq!(fs::read_to_string(untracked).unwrap(), UNFORMATTED);
}

#[test]
fn write_outside_git_fails_closed_without_override() {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let root = std::env::temp_dir().join(format!(
        "oresfmt-outside-git-{}-{nonce}",
        std::process::id()
    ));
    fs::create_dir_all(&root).unwrap();
    let path = root.join("sample.ores");
    fs::write(&path, UNFORMATTED).unwrap();

    let refused = Command::new(env!("CARGO_BIN_EXE_oresfmt"))
        .current_dir(&root)
        .args(["--write", "sample.ores"])
        .output()
        .unwrap();
    assert_eq!(refused.status.code(), Some(2));
    assert_eq!(fs::read_to_string(&path).unwrap(), UNFORMATTED);

    let allowed = Command::new(env!("CARGO_BIN_EXE_oresfmt"))
        .current_dir(&root)
        .args(["--write", "--ok-to-mod-outside-git", "sample.ores"])
        .output()
        .unwrap();
    assert!(allowed.status.success());
    assert_eq!(fs::read_to_string(&path).unwrap(), FORMATTED);

    let _ = fs::remove_dir_all(root);
}

#[test]
fn write_safety_overrides_require_write_mode() {
    let repo = TempRepo::new("override-mode");
    repo.write("sample.ores", UNFORMATTED);

    let output = repo.run(&["--check", "--ok-to-mod-untracked-files", "sample.ores"]);

    assert_eq!(output.status.code(), Some(2));
    assert!(
        String::from_utf8_lossy(&output.stderr)
            .contains("write-safety override flags require --write")
    );
}

#[cfg(unix)]
#[test]
fn explicit_symlink_inputs_are_refused() {
    use std::os::unix::fs::symlink;

    let repo = TempRepo::new("symlink");
    let target = repo.write("target.ores", UNFORMATTED);
    let link = repo.root.join("link.ores");
    symlink(&target, &link).unwrap();

    let output = repo.run(&["link.ores"]);

    assert_eq!(output.status.code(), Some(2));
    assert_eq!(fs::read_to_string(target).unwrap(), UNFORMATTED);
    assert!(String::from_utf8_lossy(&output.stderr).contains("refusing explicit symlink input"));
}

#[cfg(unix)]
#[test]
fn recursive_walk_skips_symlinked_ores_files() {
    use std::os::unix::fs::symlink;

    let repo = TempRepo::new("walk-symlink");
    let real = repo.write("real.ores", FORMATTED);
    let external_root = std::env::temp_dir().join(format!(
        "oresfmt-external-{}",
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    fs::create_dir_all(&external_root).unwrap();
    let external = external_root.join("external.ores");
    fs::write(&external, UNFORMATTED).unwrap();
    symlink(&external, repo.root.join("linked.ores")).unwrap();

    let output = repo.run(&["."]);

    assert!(output.status.success());
    assert_eq!(fs::read_to_string(real).unwrap(), FORMATTED);
    assert_eq!(fs::read_to_string(&external).unwrap(), UNFORMATTED);

    let _ = fs::remove_dir_all(external_root);
}

#[cfg(unix)]
#[test]
fn explicit_paths_through_symlinked_directories_are_refused() {
    use std::os::unix::fs::symlink;

    let repo = TempRepo::new("symlink-parent");
    let real_dir = repo.root.join("real");
    fs::create_dir_all(&real_dir).unwrap();
    let target = real_dir.join("sample.ores");
    fs::write(&target, UNFORMATTED).unwrap();
    let alias = repo.root.join("alias");
    symlink(&real_dir, &alias).unwrap();

    let output = repo.run(&["alias/sample.ores"]);

    assert_eq!(output.status.code(), Some(2));
    assert_eq!(fs::read_to_string(target).unwrap(), UNFORMATTED);
    assert!(
        String::from_utf8_lossy(&output.stderr)
            .contains("refusing path through symlinked directory")
    );
}

#[test]
fn end_of_options_allows_dash_prefixed_paths() {
    let repo = TempRepo::new("dash-path");
    let path = repo.write("-sample.ores", UNFORMATTED);

    let refused = repo.run(&["--check", "-sample.ores"]);
    assert_eq!(refused.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&refused.stderr).contains("unknown option"));

    let accepted = repo.run(&["--check", "--", "-sample.ores"]);
    assert_eq!(accepted.status.code(), Some(1));
    assert_eq!(fs::read_to_string(path).unwrap(), UNFORMATTED);
    assert!(String::from_utf8_lossy(&accepted.stderr).contains("needs formatting"));
}

#[cfg(unix)]
#[test]
fn non_utf8_paths_are_preserved_as_os_strings() {
    use std::ffi::OsString;
    use std::os::unix::ffi::OsStringExt;

    let repo = TempRepo::new("non-utf8-path");
    let name = OsString::from_vec(b"sample-\xff.ores".to_vec());
    let path = repo.root.join(&name);
    let created = fs::write(&path, UNFORMATTED);
    let args = [OsStr::new("--check"), OsStr::new("--"), name.as_os_str()];

    let output = repo.run_os(&args);

    if let Err(error) = created {
        // APFS rejects invalid UTF-8 names before the CLI can inspect them.
        // Still exercise OsString argument handling and require a clean error.
        assert_eq!(error.raw_os_error(), Some(92)); // EILSEQ on Darwin
        assert_eq!(output.status.code(), Some(2));
        assert!(!path.exists());
    } else {
        assert_eq!(output.status.code(), Some(1));
        assert_eq!(fs::read_to_string(path).unwrap(), UNFORMATTED);
    }
}

#[test]
fn write_replaces_only_requested_hardlink_path() {
    let repo = TempRepo::new("hardlink-isolation");
    let target = repo.write("sample.ores", UNFORMATTED);
    let other = repo.root.join("hardlinked-copy.ores");
    fs::hard_link(&target, &other).unwrap();
    repo.commit_all("hardlink baseline");

    let output = repo.run(&["--write", "sample.ores"]);
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(fs::read_to_string(target).unwrap(), FORMATTED);
    assert_eq!(
        fs::read_to_string(other).unwrap(),
        UNFORMATTED,
        "formatting one hard-linked path must not mutate its sibling"
    );
}

#[test]
fn equivalent_input_paths_are_processed_once() {
    let repo = TempRepo::new("path-alias");
    let target = repo.write("sample.ores", UNFORMATTED);
    repo.commit_all("baseline");

    let output = repo.run(&["--write", "sample.ores", "./sample.ores"]);
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(fs::read_to_string(target).unwrap(), FORMATTED);
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert_eq!(stdout.matches("formatted ").count(), 1);
}

#[cfg(unix)]
#[test]
fn stdout_rejects_symlinks_and_symlinked_parents() {
    use std::os::unix::fs::symlink;

    let repo = TempRepo::new("stdout-symlinks");
    repo.write("src/example.ores", UNFORMATTED);
    let leaf = repo.root.join("link.ores");
    symlink(repo.root.join("src/example.ores"), &leaf).unwrap();

    let leaf_result = repo.run(&["--stdout", "link.ores"]);
    assert_eq!(leaf_result.status.code(), Some(2));
    assert!(leaf_result.stdout.is_empty());

    let linked_directory = repo.root.join("dirlink");
    symlink(repo.root.join("src"), &linked_directory).unwrap();
    let parent_result = repo.run(&["--stdout", "dirlink/example.ores"]);
    assert_eq!(parent_result.status.code(), Some(2));
    assert!(parent_result.stdout.is_empty());
}

#[cfg(unix)]
#[test]
fn atomic_write_preserves_existing_file_mode() {
    use std::os::unix::fs::PermissionsExt;

    let repo = TempRepo::new("file-permissions");
    let target = repo.write("sample.ores", UNFORMATTED);
    fs::set_permissions(&target, fs::Permissions::from_mode(0o640)).unwrap();
    repo.commit_all("baseline");

    let output = repo.run(&["--write", "sample.ores"]);
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let mode = fs::metadata(&target).unwrap().permissions().mode() & 0o777;
    assert_eq!(mode, 0o640);
}
