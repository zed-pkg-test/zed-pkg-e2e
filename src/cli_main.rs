use oreslang_format::{FormatError, format_source, is_formatted};
use std::env;
use std::ffi::OsStr;
use std::fs::{self, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Component, Path, PathBuf};
use std::process::{Command, ExitCode};

const HELP: &str = r#"oresfmt - the canonical Oreslang formatter

USAGE:
    oresfmt [--dry-run | --write | --check | --stdout] [safety flags] <path>...
    oresfmt [--check] -

MODES:
    --dry-run   Preview changes without modifying files (default)
    --write     Rewrite files in place after Git safety checks
    --check     Do not write files; exit 1 if any input is not canonical
    --stdout    Print one formatted file to stdout instead of writing it

WRITE SAFETY:
    --ok-to-mod-dirty-files
                Allow --write to modify tracked files with staged/unstaged changes
    --ok-to-mod-untracked-files
                Allow --write to modify untracked or ignored files
    --ok-to-mod-outside-git
                Allow --write outside a Git worktree

OTHER:
    -h, --help  Print help
    -V, --version
                Print version
    --          Stop option parsing; remaining arguments are filesystem paths

PATHS:
    Files are formatted directly. Directories are traversed recursively and
    only *.ores files are selected. '-' reads stdin and writes stdout.

There are intentionally no style flags or configuration files.
"#;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Mode {
    DryRun,
    Write,
    Check,
    Stdout,
}

#[derive(Debug, Clone, Copy, Default)]
struct WritePolicy {
    allow_dirty_tracked: bool,
    allow_untracked: bool,
    allow_outside_git: bool,
}

#[derive(Debug)]
enum GitFileState {
    CleanTracked,
    DirtyTracked(String),
    UntrackedOrIgnored,
    OutsideGit,
}

fn main() -> ExitCode {
    match run() {
        Ok(code) => code,
        Err(err) => {
            eprintln!("oresfmt: {err}");
            // Source syntax/formatting rejections are distinct from tool errors.
            ExitCode::from(if err.is::<FormatError>() { 1 } else { 2 })
        }
    }
}

fn run() -> Result<ExitCode, Box<dyn std::error::Error>> {
    let mut mode = Mode::DryRun;
    let mut explicit_mode: Option<&'static str> = None;
    let mut policy = WritePolicy::default();
    let mut paths = Vec::new();
    let mut parse_options = true;

    for arg in env::args_os().skip(1) {
        if parse_options && arg == OsStr::new("--") {
            parse_options = false;
            continue;
        }

        if parse_options {
            if let Some(arg) = arg.to_str() {
                match arg {
                    "--dry-run" => {
                        select_mode(&mut mode, &mut explicit_mode, Mode::DryRun, "--dry-run")?
                    }
                    "--write" => {
                        select_mode(&mut mode, &mut explicit_mode, Mode::Write, "--write")?
                    }
                    "--check" => {
                        select_mode(&mut mode, &mut explicit_mode, Mode::Check, "--check")?
                    }
                    "--stdout" => {
                        select_mode(&mut mode, &mut explicit_mode, Mode::Stdout, "--stdout")?
                    }
                    "--ok-to-mod-dirty-files" => policy.allow_dirty_tracked = true,
                    "--ok-to-mod-untracked-files" => policy.allow_untracked = true,
                    "--ok-to-mod-outside-git" => policy.allow_outside_git = true,
                    "-h" | "--help" => {
                        io::stdout().write_all(HELP.as_bytes())?;
                        return Ok(ExitCode::SUCCESS);
                    }
                    "-V" | "--version" => {
                        writeln!(io::stdout(), "oresfmt {}", env!("CARGO_PKG_VERSION"))?;
                        return Ok(ExitCode::SUCCESS);
                    }
                    _ if arg.starts_with('-') && arg != "-" => {
                        return Err(format!("unknown option: {arg}").into());
                    }
                    _ => {
                        paths.push(PathBuf::from(arg));
                        continue;
                    }
                }
                continue;
            }
        }

        // Non-UTF-8 arguments are valid filesystem paths. They cannot match
        // textual options, so preserve their raw OsString bytes.
        paths.push(PathBuf::from(arg));
    }

    if paths.is_empty() {
        return Err(
            "no input paths; pass one or more .ores files/directories, or '-' for stdin".into(),
        );
    }

    if mode != Mode::Write
        && (policy.allow_dirty_tracked || policy.allow_untracked || policy.allow_outside_git)
    {
        return Err("write-safety override flags require --write".into());
    }

    let has_stdin = paths
        .iter()
        .any(|path| path.as_os_str() == std::ffi::OsStr::new("-"));
    if has_stdin && paths.len() != 1 {
        return Err("'-' cannot be combined with filesystem paths".into());
    }

    if has_stdin {
        if mode == Mode::Write {
            return Err("--write cannot be used with stdin".into());
        }

        let mut input = String::new();
        io::stdin().read_to_string(&mut input)?;
        let formatted = format_source(&input)?;

        if mode == Mode::Check {
            return Ok(if formatted == input {
                ExitCode::SUCCESS
            } else {
                ExitCode::from(1)
            });
        }

        io::stdout().write_all(formatted.as_bytes())?;
        return Ok(ExitCode::SUCCESS);
    }

    if mode == Mode::Stdout {
        if paths.len() != 1 {
            return Err("--stdout requires exactly one filesystem file".into());
        }
        let metadata = fs::symlink_metadata(&paths[0])?;
        if metadata.file_type().is_symlink() {
            return Err(format!("refusing explicit symlink input: {}", paths[0].display()).into());
        }
        reject_symlinked_ancestors(&paths[0])?;
        if !metadata.is_file() {
            return Err("--stdout requires exactly one filesystem file".into());
        }
        let input = fs::read_to_string(&paths[0])?;
        let formatted = format_source(&input)?;
        io::stdout().write_all(formatted.as_bytes())?;
        return Ok(ExitCode::SUCCESS);
    }

    let files = collect_files(&paths)?;

    match mode {
        Mode::DryRun => {
            for path in files {
                let input = fs::read_to_string(&path)?;
                if !is_formatted(&input)? {
                    println!("would format {}", path.display());
                }
            }
            Ok(ExitCode::SUCCESS)
        }
        Mode::Check => {
            let mut dirty = false;
            for path in files {
                let input = fs::read_to_string(&path)?;
                if !is_formatted(&input)? {
                    eprintln!("needs formatting: {}", path.display());
                    dirty = true;
                }
            }
            Ok(if dirty {
                ExitCode::from(1)
            } else {
                ExitCode::SUCCESS
            })
        }
        Mode::Write => write_files(files, policy),
        Mode::Stdout => unreachable!("handled above"),
    }
}

fn select_mode(
    mode: &mut Mode,
    explicit_mode: &mut Option<&'static str>,
    next: Mode,
    flag: &'static str,
) -> Result<(), Box<dyn std::error::Error>> {
    if let Some(previous) = *explicit_mode {
        if previous != flag {
            return Err(format!("{previous} cannot be combined with {flag}").into());
        }
    }

    *mode = next;
    *explicit_mode = Some(flag);
    Ok(())
}

fn write_files(
    files: Vec<PathBuf>,
    policy: WritePolicy,
) -> Result<ExitCode, Box<dyn std::error::Error>> {
    let mut changes = Vec::new();

    for path in files {
        let original_metadata = fs::symlink_metadata(&path)?;
        let input = fs::read_to_string(&path)?;
        #[cfg(unix)]
        check_source_identity(&path, &original_metadata)?;
        let formatted = format_source(&input)?;
        if formatted != input {
            changes.push((path, input, formatted, original_metadata));
        }
    }

    // Validate the entire write set first. A refusal on the last file must not
    // leave the workspace half-formatted.
    for (path, _, _, _) in &changes {
        enforce_write_policy(path, policy)?;
    }

    // Preflight metadata for every changed file before writing any file.
    // In particular, refuse ACL/xattr-bearing files to prevent lossy renames.
    for (path, _, _, metadata) in &changes {
        reject_unsupported_file_metadata(path)?;
        #[cfg(unix)]
        check_source_identity(path, metadata)?;
    }

    // Re-read the whole write set after Git preflight. This catches editors,
    // generators, hooks, or path substitutions that changed a target while
    // formatting was being prepared, before any write begins.
    for (path, original, _, metadata) in &changes {
        revalidate_write_target(path, original)?;
        #[cfg(unix)]
        check_source_identity(path, metadata)?;
    }

    for (path, original, formatted, metadata) in changes {
        // Narrow the remaining TOCTOU window by validating each target again
        // immediately before its write. In particular, refuse if a path or one
        // of its parent directories was replaced by a symlink after preflight.
        revalidate_write_target(&path, &original)?;
        #[cfg(unix)]
        check_source_identity(&path, &metadata)?;
        replace_atomically(&path, &original, &formatted, &metadata)?;
        println!("formatted {}", path.display());
    }

    Ok(ExitCode::SUCCESS)
}


#[cfg(unix)]
fn check_source_identity(
    path: &Path,
    original: &fs::Metadata,
) -> Result<(), Box<dyn std::error::Error>> {
    use std::os::unix::fs::MetadataExt;

    let current = fs::symlink_metadata(path)?;
    if !current.is_file()
        || current.dev() != original.dev()
        || current.ino() != original.ino()
        || current.uid() != original.uid()
        || current.gid() != original.gid()
        || current.mode() != original.mode()
        || current.size() != original.size()
        || current.mtime() != original.mtime()
        || current.mtime_nsec() != original.mtime_nsec()
        || current.ctime() != original.ctime()
        || current.ctime_nsec() != original.ctime_nsec()
    {
        return Err(format!(
            "refusing source inode or metadata changed during preflight: {}",
            path.display()
        )
        .into());
    }
    Ok(())
}

#[cfg(unix)]
fn check_parent_identity(
    parent: &Path,
    original: &fs::Metadata,
) -> Result<(), Box<dyn std::error::Error>> {
    use std::os::unix::fs::MetadataExt;
    let current = fs::symlink_metadata(parent)?;
    if !current.is_dir() || current.dev() != original.dev() || current.ino() != original.ino() {
        return Err(format!(
            "refusing directory replaced during atomic preflight: {}",
            parent.display()
        ).into());
    }
    Ok(())
}

#[cfg(target_os = "linux")]
fn reject_unsupported_file_metadata(path: &Path) -> Result<(), Box<dyn std::error::Error>> {
    use std::os::fd::AsRawFd;
    use std::os::raw::{c_char, c_int};
    use std::os::unix::fs::{MetadataExt, OpenOptionsExt};

    unsafe extern "C" {
        fn flistxattr(fd: c_int, list: *mut c_char, size: usize) -> isize;
    }

    // Linux O_NOFOLLOW: never inspect an unintended symlink destination.
    const O_NOFOLLOW: i32 = 0o400000;
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(O_NOFOLLOW)
        .open(path)?;
    let fd_metadata = file.metadata()?;
    let path_metadata = fs::symlink_metadata(path)?;
    if !path_metadata.is_file()
        || fd_metadata.dev() != path_metadata.dev()
        || fd_metadata.ino() != path_metadata.ino()
    {
        return Err(format!("refusing source path replaced while inspecting metadata: {}", path.display()).into());
    }

    // A null buffer with zero capacity asks only for the required byte count.
    // This inspects all Linux namespaces, including ACL and security xattrs.
    let bytes = unsafe { flistxattr(file.as_raw_fd(), std::ptr::null_mut(), 0) };
    if bytes < 0 {
        return Err(format!(
            "cannot verify source extended attributes for {}: {}",
            path.display(),
            io::Error::last_os_error()
        ).into());
    }
    if bytes > 0 {
        return Err(format!(
            "refusing atomic replacement of file with extended attributes or ACLs: {}",
            path.display()
        ).into());
    }
    Ok(())
}

// Platforms without a verified metadata-preserving replacement implementation
// must fail closed rather than silently strip ACLs or extended attributes.
#[cfg(not(target_os = "linux"))]
fn reject_unsupported_file_metadata(path: &Path) -> Result<(), Box<dyn std::error::Error>> {
    Err(format!(
        "atomic --write is not yet validated for metadata preservation on this platform: {}",
        path.display()
    ).into())
}

// Avoid truncating the destination on short writes, disk exhaustion or other
// write errors. Replacing the directory entry also avoids mutating an
// unexpected hard-linked file outside the caller's requested path.
//
// This is a per-file atomic replacement, NOT a multi-file transaction.
// Symlinked parent paths are preflighted, but fully hostile concurrent
// directory replacement requires dirfd/openat-style confinement.
fn replace_atomically(
    path: &Path,
    original: &str,
    formatted: &str,
    initial_metadata: &fs::Metadata,
) -> Result<(), Box<dyn std::error::Error>> {
    use std::time::{SystemTime, UNIX_EPOCH};

    revalidate_write_target(path, original)?;
    reject_unsupported_file_metadata(path)?;
    #[cfg(unix)]
    check_source_identity(path, initial_metadata)?;
    let original_metadata = fs::symlink_metadata(path)?;
    let permissions = original_metadata.permissions();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if permissions.mode() & 0o7000 != 0 {
            return Err(format!(
                "refusing atomic replacement of file with special Unix permission bits: {}",
                path.display()
            )
            .into());
        }
    }
    let parent = path.parent().unwrap_or_else(|| Path::new("."));
    #[cfg(unix)]
    let parent_handle = fs::File::open(parent)?;
    #[cfg(unix)]
    let parent_metadata = parent_handle.metadata()?;
    #[cfg(unix)]
    check_parent_identity(parent, &parent_metadata)?;
    let name = path.file_name().ok_or("write target has no filename")?;
    let nonce = SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos();

    for attempt in 0..128u32 {
        let temp_path = parent.join(format!(
            ".{}.oresfmt-{}-{nonce}-{attempt}.tmp",
            name.to_string_lossy(),
            std::process::id()
        ));
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        // Newly created source files must not become world-readable even
        // briefly before the destination's original mode is restored.
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let file = options.open(&temp_path);
        let mut file = match file {
            Ok(file) => file,
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error.into()),
        };
        let result = (|| -> Result<(), Box<dyn std::error::Error>> {
            file.write_all(formatted.as_bytes())?;
            #[cfg(unix)]
            {
                use std::os::unix::fs::MetadataExt;
                let staged = file.metadata()?;
                if original_metadata.uid() != staged.uid()
                    || original_metadata.gid() != staged.gid()
                {
                    return Err(format!(
                        "refusing atomic replacement that changes owner/group: {}",
                        path.display()
                    )
                    .into());
                }
            }
            file.set_permissions(permissions.clone())?;
            file.sync_all()?;
            drop(file);
            // Recheck immediately before replacing the directory entry.
            revalidate_write_target(path, original)?;
            #[cfg(unix)]
            check_source_identity(path, initial_metadata)?;
            reject_unsupported_file_metadata(path)?;
            #[cfg(unix)]
            check_parent_identity(parent, &parent_metadata)?;
            fs::rename(&temp_path, path)?;
            // Ensure the directory entry update is durable as well as the
            // already-synced file contents. A failed sync is reported, but
            // an already completed rename cannot be rolled back atomically.
            #[cfg(unix)]
            parent_handle.sync_all()?;
            Ok(())
        })();
        if result.is_err() {
            let _ = fs::remove_file(&temp_path);
        }
        return result;
    }
    Err(format!("unable to allocate unique temporary file for {}", path.display()).into())
}

fn revalidate_write_target(
    path: &Path,
    original: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    reject_symlinked_ancestors(path)?;

    let metadata = fs::symlink_metadata(path)
        .map_err(|err| format!("cannot re-inspect write target {}: {err}", path.display()))?;
    if metadata.file_type().is_symlink() {
        return Err(format!(
            "refusing to overwrite write target replaced by symlink: {}",
            path.display()
        )
        .into());
    }
    if !metadata.is_file() {
        return Err(format!(
            "refusing to overwrite write target that is no longer a regular file: {}",
            path.display()
        )
        .into());
    }

    let current = fs::read_to_string(path)?;
    if current.as_bytes() != original.as_bytes() {
        return Err(format!(
            "refusing to overwrite file changed during formatter preflight: {}",
            path.display()
        )
        .into());
    }

    Ok(())
}

fn reject_symlinked_ancestors(path: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let Some(parent) = path.parent() else {
        return Ok(());
    };

    let mut current = if parent.is_absolute() {
        PathBuf::new()
    } else {
        env::current_dir()?
    };

    for component in parent.components() {
        match component {
            Component::Prefix(prefix) => current.push(prefix.as_os_str()),
            Component::RootDir => current.push(component.as_os_str()),
            Component::CurDir => {}
            Component::ParentDir => {
                current.pop();
            }
            Component::Normal(part) => {
                current.push(part);
                let metadata = fs::symlink_metadata(&current).map_err(|err| {
                    format!(
                        "cannot inspect path component {} for {}: {err}",
                        current.display(),
                        path.display()
                    )
                })?;
                if metadata.file_type().is_symlink() {
                    return Err(format!(
                        "refusing path through symlinked directory: {} (input {})",
                        current.display(),
                        path.display()
                    )
                    .into());
                }
            }
        }
    }

    Ok(())
}

fn enforce_write_policy(
    path: &Path,
    policy: WritePolicy,
) -> Result<(), Box<dyn std::error::Error>> {
    match git_file_state(path)? {
        GitFileState::CleanTracked => Ok(()),
        GitFileState::DirtyTracked(status) if policy.allow_dirty_tracked => {
            eprintln!(
                "warning: modifying dirty tracked file: {} ({status})",
                path.display()
            );
            Ok(())
        }
        GitFileState::DirtyTracked(status) => Err(format!(
            "refusing to modify dirty tracked file: {} ({status}); commit/stash/revert it, or pass --ok-to-mod-dirty-files",
            path.display()
        )
        .into()),
        GitFileState::UntrackedOrIgnored if policy.allow_untracked => {
            eprintln!(
                "warning: modifying untracked/ignored file: {}",
                path.display()
            );
            Ok(())
        }
        GitFileState::UntrackedOrIgnored => Err(format!(
            "refusing to modify untracked or ignored file: {}; add+commit it, or pass --ok-to-mod-untracked-files",
            path.display()
        )
        .into()),
        GitFileState::OutsideGit if policy.allow_outside_git => {
            eprintln!(
                "warning: modifying file outside a Git worktree: {}",
                path.display()
            );
            Ok(())
        }
        GitFileState::OutsideGit => Err(format!(
            "refusing to modify file outside a Git worktree: {}; pass --ok-to-mod-outside-git to override",
            path.display()
        )
        .into()),
    }
}

fn git_file_state(path: &Path) -> Result<GitFileState, Box<dyn std::error::Error>> {
    let absolute = fs::canonicalize(path)?;
    let parent = absolute
        .parent()
        .ok_or_else(|| format!("cannot determine parent directory for {}", path.display()))?;

    let root_output = Command::new("git")
        .arg("-C")
        .arg(parent)
        .args(["rev-parse", "--show-toplevel"])
        .output()?;

    if !root_output.status.success() {
        return Ok(GitFileState::OutsideGit);
    }

    let root_text = String::from_utf8(root_output.stdout)?;
    let root = fs::canonicalize(root_text.trim())?;
    let relative = absolute.strip_prefix(&root).map_err(|_| {
        format!(
            "Git reported worktree root {} but file {} is outside it",
            root.display(),
            absolute.display()
        )
    })?;

    let tracked = Command::new("git")
        .arg("-C")
        .arg(&root)
        .args(["ls-files", "--error-unmatch", "--"])
        .arg(relative)
        .output()?;

    if !tracked.status.success() {
        return Ok(GitFileState::UntrackedOrIgnored);
    }

    let status = Command::new("git")
        .arg("-C")
        .arg(&root)
        .args(["status", "--porcelain=v1", "--untracked-files=no", "--"])
        .arg(relative)
        .output()?;

    if !status.status.success() {
        return Err(format!("git status failed for {}", path.display()).into());
    }

    let status_text = String::from_utf8(status.stdout)?;
    let status_text = status_text.trim();
    if status_text.is_empty() {
        Ok(GitFileState::CleanTracked)
    } else {
        Ok(GitFileState::DirtyTracked(status_text.to_string()))
    }
}

fn collect_files(inputs: &[PathBuf]) -> Result<Vec<PathBuf>, Box<dyn std::error::Error>> {
    let mut files = Vec::new();
    for path in inputs {
        collect_path(path, &mut files)?;
    }
    files.sort();
    files.dedup();
    // Paths such as "a.ores" and "./a.ores" are lexically different but
    // designate the same target. Do not rewrite them twice: a second rewrite
    // would otherwise fail the expected-content revalidation after the first.
    let mut canonical = std::collections::HashSet::new();
    let mut unique = Vec::with_capacity(files.len());
    for path in files {
        if canonical.insert(fs::canonicalize(&path)?) {
            unique.push(path);
        }
    }
    let files = unique;
    if files.is_empty() {
        return Err("no .ores files found".into());
    }
    Ok(files)
}

fn collect_path(path: &Path, out: &mut Vec<PathBuf>) -> Result<(), Box<dyn std::error::Error>> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|err| format!("cannot inspect {}: {err}", path.display()))?;

    if metadata.file_type().is_symlink() {
        return Err(format!(
            "refusing explicit symlink input: {}; pass the real path instead",
            path.display()
        )
        .into());
    }
    reject_symlinked_ancestors(path)?;
    if metadata.is_file() {
        out.push(path.to_path_buf());
        return Ok(());
    }
    if !metadata.is_dir() {
        return Err(format!("unsupported input path: {}", path.display()).into());
    }

    let mut entries = fs::read_dir(path)?.collect::<Result<Vec<_>, _>>()?;
    entries.sort_by_key(|entry| entry.file_name());

    for entry in entries {
        let child = entry.path();
        let name = entry.file_name();
        let file_type = entry.file_type()?;

        // Never follow symlinks discovered during a recursive walk. Besides
        // avoiding directory cycles, this prevents a repository-local path
        // from causing writes to a target outside the requested tree.
        if file_type.is_symlink() {
            continue;
        }
        if file_type.is_dir() && matches!(name.to_str(), Some(".git" | "target" | ".cache")) {
            continue;
        }
        if file_type.is_dir() {
            collect_path(&child, out)?;
        } else if file_type.is_file() && child.extension().is_some_and(|ext| ext == "ores") {
            out.push(child);
        }
    }

    Ok(())
}

#[cfg(test)]
mod write_safety_tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn scratch(label: &str) -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = env::temp_dir().join(format!(
            "oresfmt-write-safety-{label}-{}-{nonce}",
            std::process::id()
        ));
        fs::create_dir_all(&root).unwrap();
        // macOS temp_dir traverses /var -> /private/var. Resolve only our
        // fixture root so the write guard reaches the mutation under test.
        fs::canonicalize(root).unwrap()
    }

    #[test]
    fn revalidation_rejects_changed_contents() {
        let root = scratch("changed");
        let path = root.join("sample.ores");
        fs::write(&path, "before").unwrap();
        fs::write(&path, "after").unwrap();

        let err = revalidate_write_target(&path, "before").unwrap_err();
        assert!(err.to_string().contains("changed during formatter preflight"));

        let _ = fs::remove_dir_all(root);
    }

    #[cfg(unix)]
    #[test]
    fn revalidation_rejects_leaf_replaced_by_symlink() {
        use std::os::unix::fs::symlink;

        let root = scratch("leaf-symlink");
        let path = root.join("sample.ores");
        let target = root.join("target.ores");
        fs::write(&path, "before").unwrap();
        fs::write(&target, "external").unwrap();
        fs::remove_file(&path).unwrap();
        symlink(&target, &path).unwrap();

        let err = revalidate_write_target(&path, "before").unwrap_err();
        assert!(err.to_string().contains("replaced by symlink"));
        assert_eq!(fs::read_to_string(target).unwrap(), "external");

        let _ = fs::remove_dir_all(root);
    }
    #[cfg(unix)]
    #[test]
    fn inode_guard_rejects_replacement_with_identical_bytes() {
        let root = scratch("same-bytes-different-inode");
        let path = root.join("sample.ores");
        let moved = root.join("original.ores");
        fs::write(&path, "before").unwrap();
        let original = fs::symlink_metadata(&path).unwrap();

        fs::rename(&path, &moved).unwrap();
        fs::write(&path, "before").unwrap();

        let err = check_source_identity(&path, &original).unwrap_err();
        assert!(err.to_string().contains("inode or metadata changed"));

        let _ = fs::remove_dir_all(root);
    }

    #[cfg(unix)]
    #[test]
    fn parent_guard_rejects_replaced_directory() {
        let root = scratch("replaced-parent");
        let parent = root.join("src");
        let displaced = root.join("original-src");
        fs::create_dir(&parent).unwrap();
        let original = fs::symlink_metadata(&parent).unwrap();

        fs::rename(&parent, &displaced).unwrap();
        fs::create_dir(&parent).unwrap();

        let err = check_parent_identity(&parent, &original).unwrap_err();
        assert!(err.to_string().contains("directory replaced"));

        let _ = fs::remove_dir_all(root);
    }

}
