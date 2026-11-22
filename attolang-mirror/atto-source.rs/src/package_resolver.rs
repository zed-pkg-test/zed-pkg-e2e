//! Trusted host-side resolver for a single filesystem Atto package.
//!
//! The browser wasm32 compiler does not read the filesystem. This API is
//! intended for host compilers with a known local package root. It verifies
//! lexical paths, physical containment, symlink-free path components, and a
//! strict bounded subset of .atto.pkg.toml before using compile_linked().
//!
//! This is NOT a dependency registry resolver or a race-proof hostile-FS
//! sandbox. A caller must ensure the package tree is not concurrently
//! replaced while compiling; openat2/capability directory handles are
//! required before adversarial concurrently mutable trees are supported.
use crate::diag::{Diagnostic, Pos};
use crate::linker::{self, LinkUnit};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

type R<T> = Result<T, Diagnostic>;
const MAX_FILES: usize = 64;
const MAX_MANIFEST: usize = 8 * 1024;

fn error(code: &'static str, message: impl Into<String>) -> Diagnostic {
    Diagnostic::new(Pos { line: 1, col: 1 }, code, message)
}
fn io_error(what: &str, e: std::io::Error) -> Diagnostic {
    error("E_IMPORT_PATH", format!("{what}: {e}"))
}
fn package_segment(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 64
        && s.is_ascii()
        && s.as_bytes()[0].is_ascii_alphanumeric()
        && s.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-' | b'.'))
}
fn version_ok(s: &str) -> bool {
    s.len() <= 48
        && s.is_ascii()
        && s.split('.').count() >= 3
        && s.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'.'))
        && s.as_bytes().first().is_some_and(u8::is_ascii_digit)
}

/// Strict TOML subset: one [package] table with name/version string keys.
/// Unknown or duplicate keys, other tables, escaped strings and interpolation
/// are intentionally unsupported rather than silently ignored.
pub fn parse_package_identity(manifest: &str) -> R<String> {
    if manifest.len() > MAX_MANIFEST || manifest.contains('\0') {
        return Err(error("E_MANIFEST", "manifest exceeds budget or contains NUL"));
    }
    let mut in_package = false;
    let (mut name, mut version): (Option<String>, Option<String>) = (None, None);
    for (i, raw) in manifest.lines().enumerate() {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if line == "[package]" {
            if in_package || name.is_some() || version.is_some() {
                return Err(error("E_MANIFEST", format!("duplicate package table at line {}", i + 1)));
            }
            in_package = true;
            continue;
        }
        if line.starts_with('[') || !in_package {
            return Err(error("E_MANIFEST", format!("unsupported manifest table at line {}", i + 1)));
        }
        let (key, value) = line
            .split_once('=')
            .ok_or_else(|| error("E_MANIFEST", format!("expected key=value at line {}", i + 1)))?;
        let key = key.trim();
        let value = value.trim();
        let value = value
            .strip_prefix('"')
            .ok_or_else(|| error("E_MANIFEST", "package values must be quoted ASCII strings"))?;
        let end = value.find('"').ok_or_else(|| error("E_MANIFEST", "unterminated package string"))?;
        let (raw_value, suffix) = value.split_at(end);
        if raw_value.contains('\\') || !raw_value.is_ascii() || (!suffix[1..].trim().is_empty() && !suffix[1..].trim().starts_with('#')) {
            return Err(error("E_MANIFEST", "unsupported package value or inline content"));
        }
        match key {
            "name" if name.is_none() => name = Some(raw_value.to_string()),
            "version" if version.is_none() => version = Some(raw_value.to_string()),
            _ => return Err(error("E_MANIFEST", "unknown or duplicate package key")),
        }
    }
    let name = name.ok_or_else(|| error("E_MANIFEST", "missing [package].name"))?;
    let version = version.ok_or_else(|| error("E_MANIFEST", "missing [package].version"))?;
    if !package_segment(&name) || !version_ok(&version) {
        return Err(error("E_MANIFEST", "invalid package name or version"));
    }
    let identity = format!("{name}@{version}");
    if identity.len() > 128 {
        return Err(error("E_MANIFEST", "canonical package identity exceeds budget"));
    }
    Ok(identity)
}

/// Verify every physical path component and containment, not just string
/// prefixes. No source read is permitted through a symlink or outside root.
#[cfg(not(target_os = "linux"))]
fn checked_path(root: &Path, relative: &str) -> R<PathBuf> {
    if !linker::path_ok(relative) {
        return Err(error("E_IMPORT_PATH", "noncanonical package source path"));
    }
    let mut candidate = root.to_path_buf();
    for component in relative.split('/') {
        candidate.push(component);
        let meta = fs::symlink_metadata(&candidate).map_err(|e| io_error("missing package source component", e))?;
        if meta.file_type().is_symlink() {
            return Err(error("E_IMPORT_PATH", "package source path includes a symbolic link"));
        }
    }
    let physical = fs::canonicalize(&candidate).map_err(|e| io_error("cannot canonicalize package source", e))?;
    if !physical.starts_with(root) || !physical.is_file() {
        return Err(error("E_IMPORT_PATH", "package source escapes root or is not a regular file"));
    }
    Ok(physical)
}

/// Read only the entry and reachable import graph, never scan unrelated
/// directories. Every path is validated against the canonical package root.
/// Cross-package dependencies require an authenticated dependency lock and
/// are deliberately not supported by this first host resolver.
pub fn compile_package(root: &Path, entry: &str) -> R<Vec<u8>> {
    compile_package_impl(root, entry, None)
}

fn compile_package_impl(root: &Path, entry: &str, pins: Option<&LockedSources>) -> R<Vec<u8>> {
    if !linker::path_ok(entry) {
        return Err(error("E_IMPORT_PATH", "entry path must be canonical and package-relative"));
    }
    // Linux: pin and traverse directory *handles*, and read from opened file
    // descriptors. No second by-path read after a path-only validation.
    #[cfg(target_os = "linux")]
    let root_reader = crate::secure_fs::LockedRoot::open(root)?;
    #[cfg(target_os = "linux")]
    let manifest = root_reader.read(".atto.pkg.toml", MAX_MANIFEST, "E_MANIFEST")?;
    // Other platforms temporarily retain the conservative stable-tree path
    // profile and MUST NOT advertise adversarial concurrency resistance.
    #[cfg(not(target_os = "linux"))]
    let canonical = {
        let root_meta = fs::symlink_metadata(root).map_err(|e| io_error("cannot inspect package root", e))?;
        if root_meta.file_type().is_symlink() || !root_meta.is_dir() {
            return Err(error("E_IMPORT_PATH", "package root must be a directory, not a symlink"));
        }
        fs::canonicalize(root).map_err(|e| io_error("cannot canonicalize package root", e))?
    };
    #[cfg(not(target_os = "linux"))]
    let manifest = {
        let manifest_path = canonical.join(".atto.pkg.toml");
        let meta = fs::symlink_metadata(&manifest_path).map_err(|e| io_error("missing .atto.pkg.toml", e))?;
        if !meta.is_file() || meta.file_type().is_symlink() || meta.len() as usize > MAX_MANIFEST {
            return Err(error("E_MANIFEST", "manifest must be a bounded, regular non-symlink file"));
        }
        fs::read_to_string(&manifest_path).map_err(|e| io_error("cannot read .atto.pkg.toml", e))?
    };
    if let Some(pins) = pins {
        if crate::sha256::hex_digest(manifest.as_bytes()) != pins.manifest_sha256 {
            return Err(error("E_LOCK_HASH", "package manifest does not match authenticated lock digest"));
        }
    }
    let identity = parse_package_identity(&manifest)?;
    let mut pending = vec![entry.to_string()];
    let mut loaded = BTreeMap::<String, String>::new();
    let mut seen = BTreeSet::new();
    while let Some(relative) = pending.pop() {
        if !seen.insert(relative.clone()) {
            continue;
        }
        if seen.len() > MAX_FILES {
            return Err(error("E_SOURCE_LIMIT", "too many reachable package source files"));
        }
        #[cfg(target_os = "linux")]
        let src = root_reader.read(&relative, crate::SOURCE_CAP, "E_IMPORT_PATH")?;
        #[cfg(not(target_os = "linux"))]
        let src = {
            let physical = checked_path(&canonical, &relative)?;
            let size = fs::metadata(&physical)
                .map_err(|e| io_error("cannot inspect package source", e))?
                .len();
            if size > crate::SOURCE_CAP as u64 {
                return Err(error("E_SOURCE_LIMIT", "source file exceeds browser compiler limit"));
            }
            let src = fs::read_to_string(&physical).map_err(|e| io_error("cannot read package source file", e))?;
            if src.len() > crate::SOURCE_CAP {
                return Err(error("E_SOURCE_LIMIT", "package source file is too large"));
            }
            src
        };
        if let Some(pins) = pins {
            let want = pins
                .files
                .get(&relative)
                .ok_or_else(|| error("E_LOCK_HASH", format!("missing pinned source digest for '{relative}'")))?;
            if crate::sha256::hex_digest(src.as_bytes()) != *want {
                return Err(error(
                    "E_LOCK_HASH",
                    format!("source content changed after trusted lock for '{relative}'"),
                ));
            }
        }
        for child in linker::import_paths(&src, &relative)? {
            pending.push(child);
        }
        loaded.insert(relative, src);
    }
    if let Some(pins) = pins {
        if pins.files.len() != loaded.len() {
            return Err(error("E_LOCK_HASH", "trusted lockfile contains unexpected/unreachable source pins"));
        }
    }
    let units: Vec<LinkUnit<'_>> = loaded
        .iter()
        .map(|(path, source)| LinkUnit {
            path,
            source,
            package: &identity,
        })
        .collect();
    linker::compile_linked(entry, &units)
}

/// Discover the *nearest* package manifest above an entry file and return its
/// canonical root plus canonical relative entry path. This is opt-in host-only
/// behavior, not an implicit side effect of compile(source).
pub fn discover_package_root(entry: &Path) -> R<(PathBuf, String)> {
    use std::path::Component;
    let absolute = if entry.is_absolute() {
        entry.to_path_buf()
    } else {
        std::env::current_dir()
            .map_err(|e| io_error("cannot inspect current directory", e))?
            .join(entry)
    };
    // Do not traverse user-supplied path aliases or symlink components before
    // determining the physical package root. Absolute and relative canonical
    // spellings are supported; '../' input paths are deliberately rejected.
    let mut prefix = PathBuf::new();
    for item in absolute.components() {
        match item {
            Component::ParentDir => return Err(error("E_IMPORT_PATH", "entry path may not contain parent-directory traversal")),
            Component::CurDir => continue,
            _ => prefix.push(item.as_os_str()),
        }
        if prefix.as_os_str().is_empty() {
            continue;
        }
        match fs::symlink_metadata(&prefix) {
            Ok(meta) if meta.file_type().is_symlink() => {
                return Err(error("E_IMPORT_PATH", "entry path contains a symbolic link"));
            }
            Ok(_) => {}
            Err(e) => return Err(io_error("cannot inspect entry path component", e)),
        }
    }
    let physical = fs::canonicalize(&absolute).map_err(|e| io_error("cannot canonicalize package entry", e))?;
    if !physical.is_file() {
        return Err(error("E_IMPORT_PATH", "package entry is not a regular source file"));
    }
    let mut current = physical
        .parent()
        .ok_or_else(|| error("E_IMPORT_PATH", "package entry has no parent directory"))?;
    for _ in 0..64 {
        let marker = current.join(".atto.pkg.toml");
        match fs::symlink_metadata(&marker) {
            Ok(_) => {
                let relative = physical
                    .strip_prefix(current)
                    .map_err(|_| error("E_IMPORT_PATH", "entry escapes discovered package"))?
                    .to_str()
                    .ok_or_else(|| error("E_IMPORT_PATH", "non-UTF8 package relative entry path"))?
                    .replace(std::path::MAIN_SEPARATOR, "/");
                if !linker::path_ok(&relative) {
                    return Err(error("E_IMPORT_PATH", "discovered entry path is not a canonical Atto source"));
                }
                return Ok((current.to_path_buf(), relative));
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => return Err(io_error("cannot inspect ancestor package manifest", e)),
        }
        current = current
            .parent()
            .ok_or_else(|| error("E_MANIFEST", "no .atto.pkg.toml above the entry source"))?;
    }
    Err(error("E_MANIFEST", "package root discovery exceeded ancestor budget"))
}

/// Find the nearest manifest and compile through the same checked local
/// resolver used by explicit --package-root. No implicit dependency registry.
pub fn compile_discovered(entry: &Path) -> R<Vec<u8>> {
    let (root, relative) = discover_package_root(entry)?;
    compile_package(&root, &relative)
}

/// A small, non-registry, SHA-256-verified local source lock. Authenticity
/// comes ONLY from a trusted expected lockfile digest supplied by the caller;
/// an unsigned lockfile stored alongside mutable sources is not a trust root.
#[derive(Clone, Debug)]
struct LockedSources {
    manifest_sha256: String,
    files: BTreeMap<String, String>,
}
fn valid_sha256(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|x| x.is_ascii_digit() || (b'a'..=b'f').contains(&x))
}
fn lock_string(s: &str) -> R<String> {
    let x = s.trim();
    if x.len() < 2 || !x.starts_with('"') || !x.ends_with('"') || x[1..x.len() - 1].contains('"') || x[1..x.len() - 1].contains('\\') {
        return Err(error("E_LOCK", "source lockfile values must be simple quoted strings"));
    }
    Ok(x[1..x.len() - 1].to_string())
}
fn parse_source_lock(contents: &str) -> R<LockedSources> {
    if contents.len() > MAX_MANIFEST * 4 || contents.contains('\0') {
        return Err(error("E_LOCK", "source lockfile exceeds budget or contains NUL"));
    }
    let mut phase = 0;
    let mut manifest = None;
    let mut files = BTreeMap::new();
    for line in contents.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        match line {
            "[lock]" if phase == 0 => {
                phase = 1;
                continue;
            }
            "[files]" if phase == 1 => {
                phase = 2;
                continue;
            }
            _ if line.starts_with('[') => return Err(error("E_LOCK", "unexpected or duplicate lock section")),
            _ => {}
        }
        let (key, value) = line.split_once('=').ok_or_else(|| error("E_LOCK", "expected lockfile key=value"))?;
        let value = lock_string(value)?;
        if !valid_sha256(&value) {
            return Err(error("E_LOCK", "SHA-256 source digest must be 64 lowercase hex characters"));
        }
        if phase == 1 && key.trim() == "manifest_sha256" && manifest.is_none() {
            manifest = Some(value);
        } else if phase == 2 {
            let path = lock_string(key)?;
            if !linker::path_ok(&path) {
                return Err(error("E_LOCK", "lockfile source path is not canonical"));
            }
            if files.len() >= MAX_FILES || files.insert(path, value).is_some() {
                return Err(error("E_LOCK", "duplicate or excessive source locks"));
            }
        } else {
            return Err(error("E_LOCK", "unknown or duplicate trusted lock key"));
        }
    }
    Ok(LockedSources {
        manifest_sha256: manifest.ok_or_else(|| error("E_LOCK", "missing manifest digest"))?,
        files,
    })
}

/// Hash-authenticated bounded source snapshot. The *expected lock SHA-256*
/// MUST arrive over an independently trusted channel. A caller who accepts
/// an arbitrary lock digest read from the same untrusted directory has no
/// authentication. Files are still loaded through the Linux FD-rooted
/// resolver and are content-pinned before linking.
pub fn compile_package_locked(root: &Path, entry: &str, expected_lock_sha256: &str) -> R<Vec<u8>> {
    if !valid_sha256(expected_lock_sha256) {
        return Err(error("E_LOCK", "expected trusted lock digest must be a lowercase SHA-256"));
    }
    #[cfg(target_os = "linux")]
    let lock_text = crate::secure_fs::LockedRoot::open(root)?.read(".atto.pkg.lock", MAX_MANIFEST * 4, "E_LOCK")?;
    #[cfg(not(target_os = "linux"))]
    let lock_text = {
        let physical = fs::canonicalize(root).map_err(|e| io_error("cannot canonicalize lockfile root", e))?;
        let file = physical.join(".atto.pkg.lock");
        let meta = fs::symlink_metadata(&file).map_err(|e| io_error("cannot inspect lockfile", e))?;
        if !meta.is_file() || meta.file_type().is_symlink() || meta.len() as usize > MAX_MANIFEST * 4 {
            return Err(error("E_LOCK", "source lockfile is not a regular bounded file"));
        }
        fs::read_to_string(file).map_err(|e| io_error("cannot read source lockfile", e))?
    };
    if crate::sha256::hex_digest(lock_text.as_bytes()) != expected_lock_sha256 {
        return Err(error("E_LOCK_HASH", "source lockfile does not match trusted SHA-256"));
    }
    let pins = parse_source_lock(&lock_text)?;
    compile_package_impl(root, entry, Some(&pins))
}

/// Immutable source input supplied by a caller holding an authenticated lock.
#[derive(Clone, Copy, Debug)]
pub struct SnapshotSource<'a> {
    pub path: &'a str,
    pub source: &'a str,
}

/// Compile a complete, hash-authenticated source graph without filesystem IO.
/// The caller must independently authenticate the expected SHA-256 lock hash.
/// Every provided source must be reachable from the entry and pinned by the
/// lock. This avoids filesystem TOCTOU within the compiler itself.
pub fn compile_locked_snapshot(
    entry: &str,
    manifest: &str,
    lockfile: &str,
    expected_lock_sha256: &str,
    inputs: &[SnapshotSource<'_>],
) -> R<Vec<u8>> {
    if !linker::path_ok(entry) {
        return Err(error("E_IMPORT_PATH", "snapshot entry path is not canonical"));
    }
    if !valid_sha256(expected_lock_sha256) {
        return Err(error("E_LOCK", "trusted snapshot digest must be lowercase SHA-256"));
    }
    if crate::sha256::hex_digest(lockfile.as_bytes()) != expected_lock_sha256 {
        return Err(error("E_LOCK_HASH", "snapshot lockfile digest mismatch"));
    }
    let pins = parse_source_lock(lockfile)?;
    if crate::sha256::hex_digest(manifest.as_bytes()) != pins.manifest_sha256 {
        return Err(error("E_LOCK_HASH", "snapshot manifest content mismatch"));
    }
    let identity = parse_package_identity(manifest)?;
    if inputs.is_empty() || inputs.len() > MAX_FILES || inputs.len() != pins.files.len() {
        return Err(error("E_LOCK_HASH", "snapshot file count differs from trusted lock"));
    }
    let mut sources = BTreeMap::new();
    for input in inputs {
        if !linker::path_ok(input.path) {
            return Err(error("E_IMPORT_PATH", "snapshot source path is not canonical"));
        }
        if input.source.len() > crate::SOURCE_CAP {
            return Err(error("E_SOURCE_LIMIT", "snapshot source exceeds compiler limit"));
        }
        let want = pins
            .files
            .get(input.path)
            .ok_or_else(|| error("E_LOCK_HASH", format!("untrusted snapshot source '{}'", input.path)))?;
        if crate::sha256::hex_digest(input.source.as_bytes()) != *want {
            return Err(error(
                "E_LOCK_HASH",
                format!("snapshot source content mismatch for '{}'", input.path),
            ));
        }
        if sources.insert(input.path, input.source).is_some() {
            return Err(error("E_DUPLICATE", "duplicate snapshot source path"));
        }
    }
    if sources.len() != pins.files.len() {
        return Err(error("E_LOCK_HASH", "missing snapshot source"));
    }
    let mut seen = BTreeSet::new();
    let mut pending = vec![entry.to_string()];
    while let Some(path) = pending.pop() {
        if !seen.insert(path.clone()) {
            continue;
        }
        if seen.len() > MAX_FILES {
            return Err(error("E_SOURCE_LIMIT", "snapshot graph too large"));
        }
        let source = sources
            .get(path.as_str())
            .ok_or_else(|| error("E_LOCK_HASH", format!("snapshot omitted reachable source '{path}'")))?;
        for child in linker::import_paths(source, &path)? {
            pending.push(child);
        }
    }
    if seen.len() != sources.len() {
        return Err(error("E_LOCK_HASH", "snapshot includes sources unreachable from its entry"));
    }
    let units: Vec<LinkUnit<'_>> = sources
        .iter()
        .map(|(path, source)| LinkUnit {
            path,
            source,
            package: &identity,
        })
        .collect();
    linker::compile_linked(entry, &units)
}
