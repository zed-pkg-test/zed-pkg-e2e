//! Linux descriptor-relative, symlink-free package file reads.
//!
//! Every path component is opened relative to an already-open directory FD
//! with O_NOFOLLOW and O_DIRECTORY where appropriate. File content is read
//! from the returned descriptor, not via a previously checked path. This
//! removes symlink-swap and directory-replacement TOCTOU escapes for this
//! profile. It is Linux-only and needs separate audited platform ports.
use crate::diag::{Diagnostic, Pos};
use std::ffi::CString;
use std::fs::File;
use std::io::Read;
use std::os::fd::{AsRawFd, FromRawFd};
use std::os::raw::{c_char, c_int};
use std::path::{Component, Path};

type R<T> = Result<T, Diagnostic>;
const O_RDONLY: c_int = 0;
const O_DIRECTORY: c_int = 0o200000;
const O_NOFOLLOW: c_int = 0o400000;
const O_CLOEXEC: c_int = 0o2000000;

extern "C" {
    fn openat(dirfd: c_int, pathname: *const c_char, flags: c_int, ...) -> c_int;
}

fn fail(code: &'static str, detail: impl Into<String>) -> Diagnostic {
    Diagnostic::new(Pos::default(), code, detail)
}
fn open_child(directory: &File, name: &str, directory_only: bool, code: &'static str) -> R<File> {
    let c_name = CString::new(name).map_err(|_| fail(code, "NUL in package file component"))?;
    let flags = O_RDONLY | O_NOFOLLOW | O_CLOEXEC | if directory_only { O_DIRECTORY } else { 0 };
    // SAFETY: openat's name is a NUL-terminated CString, the directory FD is
    // alive for the duration of this call, and no mode is required for an
    // O_RDONLY open. Ownership of a successful raw FD transfers to File.
    let fd = unsafe { openat(directory.as_raw_fd(), c_name.as_ptr(), flags) };
    if fd < 0 {
        return Err(fail(
            code,
            format!("cannot open checked component '{name}': {}", std::io::Error::last_os_error()),
        ));
    }
    // SAFETY: the successful openat result is a new, uniquely owned FD.
    Ok(unsafe { File::from_raw_fd(fd) })
}

pub struct LockedRoot {
    directory: File,
}

impl LockedRoot {
    pub fn open(path: &Path) -> R<Self> {
        let absolute = if path.is_absolute() {
            path.to_path_buf()
        } else {
            std::env::current_dir()
                .map_err(|e| fail("E_IMPORT_PATH", format!("cannot locate current directory: {e}")))?
                .join(path)
        };
        let mut current = File::open("/").map_err(|e| fail("E_IMPORT_PATH", format!("cannot open filesystem root: {e}")))?;
        for component in absolute.components() {
            match component {
                Component::RootDir | Component::CurDir => {}
                Component::Normal(name) => {
                    let name = name
                        .to_str()
                        .ok_or_else(|| fail("E_IMPORT_PATH", "non-UTF8 package directory name"))?;
                    current = open_child(&current, name, true, "E_IMPORT_PATH")?;
                }
                _ => {
                    return Err(fail(
                        "E_IMPORT_PATH",
                        "package root must have no parent traversal or platform prefixes",
                    ))
                }
            }
        }
        if !current
            .metadata()
            .map_err(|e| fail("E_IMPORT_PATH", format!("cannot inspect checked package root: {e}")))?
            .is_dir()
        {
            return Err(fail("E_IMPORT_PATH", "package root must be a directory"));
        }
        Ok(Self { directory: current })
    }

    pub fn read(&self, relative: &str, max_bytes: usize, diagnostic_code: &'static str) -> R<String> {
        if relative.is_empty()
            || relative.starts_with('/')
            || relative.contains('\\')
            || relative.contains(':')
            || relative
                .split('/')
                .any(|part| part.is_empty() || part == "." || part == ".." || part.contains('\0'))
        {
            return Err(fail(diagnostic_code, "invalid descriptor-relative source path"));
        }
        let mut directory: Option<File> = None;
        let mut parts = relative.split('/').peekable();
        let mut file: Option<File> = None;
        while let Some(name) = parts.next() {
            let parent = directory.as_ref().unwrap_or(&self.directory);
            if parts.peek().is_some() {
                directory = Some(open_child(parent, name, true, diagnostic_code)?);
            } else {
                file = Some(open_child(parent, name, false, diagnostic_code)?);
            }
        }
        let file = file.ok_or_else(|| fail(diagnostic_code, "empty source file path"))?;
        let metadata = file
            .metadata()
            .map_err(|e| fail(diagnostic_code, format!("cannot stat pinned source FD: {e}")))?;
        if !metadata.is_file() {
            return Err(fail(diagnostic_code, "package source must be a regular file"));
        }
        if metadata.len() > max_bytes as u64 {
            return Err(fail(
                if diagnostic_code == "E_MANIFEST" {
                    "E_MANIFEST"
                } else {
                    "E_SOURCE_LIMIT"
                },
                "package source exceeds file size budget",
            ));
        }
        let mut bytes = Vec::new();
        file.take((max_bytes as u64) + 1)
            .read_to_end(&mut bytes)
            .map_err(|e| fail(diagnostic_code, format!("cannot read pinned source descriptor: {e}")))?;
        if bytes.len() > max_bytes {
            return Err(fail(
                if diagnostic_code == "E_MANIFEST" {
                    "E_MANIFEST"
                } else {
                    "E_SOURCE_LIMIT"
                },
                "package source grew beyond budget",
            ));
        }
        String::from_utf8(bytes).map_err(|_| fail(diagnostic_code, "package source is not valid UTF-8"))
    }
}

#[cfg(test)]
mod tests {
    use super::LockedRoot;
    use std::{
        fs,
        os::unix::fs::symlink,
        sync::atomic::{AtomicUsize, Ordering},
    };
    static NEXT: AtomicUsize = AtomicUsize::new(0);
    struct Temp {
        path: std::path::PathBuf,
    }
    impl Temp {
        fn new() -> Self {
            let n = NEXT.fetch_add(1, Ordering::Relaxed);
            let path = std::env::temp_dir().join(format!("atto-fd-{}-{n}", std::process::id()));
            fs::create_dir_all(&path).unwrap();
            Self { path }
        }
    }
    impl Drop for Temp {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.path);
        }
    }

    #[test]
    fn root_fd_stays_with_original_inode_after_directory_swap() {
        let t = Temp::new();
        let real = t.path.join("real");
        let evil = t.path.join("evil");
        fs::create_dir_all(real.join("lib")).unwrap();
        fs::create_dir_all(evil.join("lib")).unwrap();
        fs::write(real.join("lib/math.atto"), "original").unwrap();
        fs::write(evil.join("lib/math.atto"), "forged").unwrap();
        let handle = LockedRoot::open(&real).unwrap();
        fs::rename(&real, t.path.join("retired")).unwrap();
        symlink(&evil, &real).unwrap();
        let data = handle.read("lib/math.atto", 128, "E_IMPORT_PATH").unwrap();
        assert_eq!(data, "original", "FD pin must not follow replaced package root");
        assert_eq!(LockedRoot::open(&real).err().unwrap().code, "E_IMPORT_PATH");
    }
    #[test]
    fn intermediate_directory_symlinks_never_follow_through_fd() {
        let t = Temp::new();
        fs::create_dir_all(t.path.join("lib")).unwrap();
        fs::write(t.path.join("lib/math.atto"), "original").unwrap();
        let handle = LockedRoot::open(&t.path).unwrap();
        fs::rename(t.path.join("lib"), t.path.join("previous")).unwrap();
        symlink(t.path.join("previous"), t.path.join("lib")).unwrap();
        assert_eq!(
            handle.read("lib/math.atto", 128, "E_IMPORT_PATH").err().unwrap().code,
            "E_IMPORT_PATH"
        );
    }
    #[test]
    fn opened_file_rejects_non_regular_or_oversized() {
        let t = Temp::new();
        fs::write(t.path.join("foo.atto"), "a".repeat(100)).unwrap();
        let handle = LockedRoot::open(&t.path).unwrap();
        assert_eq!(handle.read("foo.atto", 99, "E_IMPORT_PATH").unwrap_err().code, "E_SOURCE_LIMIT");
        assert_eq!(handle.read("foo.atto/", 100, "E_IMPORT_PATH").unwrap_err().code, "E_IMPORT_PATH");
        assert_eq!(handle.read("../foo.atto", 100, "E_IMPORT_PATH").unwrap_err().code, "E_IMPORT_PATH");
    }
}
