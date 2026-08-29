#!/usr/bin/env python3
"""Assemble and validate the global-package CLI on exact current main.

This script never writes to the product repository. It overlays only the
reviewed permanent files from the superseded feature head, composes them with
the current modular CLI, applies the collision/profile rollback and offline
frozen-replay repairs found by the external canary, and leaves an ordinary
working-tree delta for independent validation and artifact publication.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


PERMANENT_FEATURE_FILES = (
    ".github/workflows/global-packages.yml",
    ".zpkg.lock",
    ".zpkg.toml",
    "docs/global-packages.md",
    "src/global.rs",
    "tests/global_cli.rs",
)

EXPECTED_CHANGED_FILES = {
    *PERMANENT_FEATURE_FILES,
    "src/completion.rs",
    "src/lib.rs",
    "src/main.rs",
    "src/ops.rs",
    # Current main inherited a small rustfmt-only delta from the standalone Nix
    # bundle merge. The complete candidate is required to repair it rather than
    # hiding a formatter failure behind the new feature.
    "src/nix_bundle_write.rs",
    "tests/nix_bundle_write_boundaries.rs",
    "tests/nix_bundle_write_cli.rs",
}


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"{label}: expected source block was not found")
    return text.replace(old, new, 1)


def insert_after(text: str, marker: str, addition: str, label: str) -> str:
    if addition in text:
        return text
    if marker not in text:
        raise RuntimeError(f"{label}: insertion point was not found")
    return text.replace(marker, marker + addition, 1)


def copy_feature_files(product: Path, feature: Path) -> None:
    for relative in PERMANENT_FEATURE_FILES:
        source = feature / relative
        destination = product / relative
        if not source.is_file():
            raise RuntimeError(f"reviewed feature file is missing: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def patch_lib(product: Path) -> None:
    path = product / "src/lib.rs"
    text = path.read_text()
    text = insert_after(
        text,
        "pub mod git_submodules;\n",
        "pub mod global;\n",
        "src/lib.rs global module",
    )
    path.write_text(text)


def patch_completion(product: Path) -> None:
    path = product / "src/completion.rs"
    text = path.read_text()
    text = replace_once(
        text,
        "use std::io;\n\nuse clap_complete::{Shell, generate};",
        "use std::io;\n\nuse anyhow::{Context, Result};\nuse clap_complete::{Shell, generate};",
        "completion result imports",
    )
    text = replace_once(
        text,
        "use crate::{dev, fetch, git_submodules, nix_bundle_write, nix_export_plan};",
        "use crate::{dev, fetch, git_submodules, global, nix_bundle_write, nix_export_plan};",
        "completion global import",
    )
    old_root = '''fn root_command() -> clap::Command {
    git_submodules::augment_root_command(nix_bundle_write::augment_root_command(
        nix_export_plan::augment_root_command(fetch::augment_root_command(
            dev::augment_root_command(cli_model::command()),
        )),
    ))
}
'''
    new_root = '''/// Build the complete public command tree shared by root help and completion
/// generation. Every modular command must compose here rather than maintaining
/// a second, partial root-help model.
pub fn root_command() -> clap::Command {
    global::augment_root_command(git_submodules::augment_root_command(
        nix_bundle_write::augment_root_command(nix_export_plan::augment_root_command(
            fetch::augment_root_command(dev::augment_root_command(cli_model::command())),
        )),
    ))
}

/// Print the complete top-level help tree.
pub fn print_root_help() -> Result<()> {
    let mut command = root_command();
    command.print_help().context("printing zed help")?;
    println!();
    Ok(())
}
'''
    text = replace_once(text, old_root, new_root, "completion root command")
    for command in ('            "global",\n', '            "bin-dir",\n'):
        if command not in text:
            text = insert_after(
                text,
                '            "overtake",\n',
                command,
                "completion command assertion",
            )
    if '            "--global-bin-dir",\n' not in text:
        text = insert_after(
            text,
            '            "--install-mode",\n',
            '            "--global-bin-dir",\n',
            "completion global-bin option assertion",
        )
    path.write_text(text)


def patch_main(product: Path) -> None:
    path = product / "src/main.rs"
    text = path.read_text()
    if not text.startswith("use std::ffi"):
        text = "use std::ffi::{OsStr, OsString};\n\n" + text
    text = insert_after(
        text,
        "use zed_cli::git_submodules as submodules;\n",
        "use zed_cli::global;\n",
        "main global import",
    )
    marker = '''    if let Err(error) = zed_cli::flags::normalize_global_boolean_environment(&args) {
        eprintln!("error: {error:#}");
        std::process::exit(2);
    }
'''
    dispatch = '''    if root_help_requested(&args) {
        if let Err(error) = completion::print_root_help() {
            eprintln!("error: {error:#}");
            std::process::exit(1);
        }
        return;
    }
    let global_requested = args.iter().skip(1).any(|argument| {
        let argument = argument.as_os_str();
        argument == OsStr::new("global") || argument == OsStr::new("--global")
    });
    if global_requested
        && let Some(result) = global::dispatch(args.clone())
    {
        match result {
            Ok(0) => return,
            Ok(code) => std::process::exit(code),
            Err(error) => {
                eprintln!("error: {error:#}");
                std::process::exit(1);
            }
        }
    }
'''
    text = insert_after(text, marker, dispatch, "main global dispatcher")
    helper_marker = "\nfn run(cli: Cli) -> anyhow::Result<()> {\n"
    helpers = '''
fn root_help_requested(args: &[OsString]) -> bool {
    let mut index = 1;
    while index < args.len() {
        let token = args[index].to_string_lossy();
        if token == "--help" || token == "-h" {
            return true;
        }
        if token == "help" {
            return args
                .iter()
                .skip(index + 1)
                .all(|argument| argument.to_string_lossy().starts_with('-'));
        }
        if root_global_option_takes_value(&token) {
            index += if token.contains('=') { 1 } else { 2 };
            continue;
        }
        if token.starts_with('-') {
            index += 1;
            continue;
        }
        return false;
    }
    false
}

fn root_global_option_takes_value(token: &str) -> bool {
    const OPTIONS: &[&str] = &[
        "--registry",
        "--home",
        "--token",
        "--auth-url",
        "--supabase-url",
        "--supabase-key",
        "--global-bin-dir",
    ];
    OPTIONS.iter().any(|option| {
        token == *option
            || token
                .strip_prefix(option)
                .is_some_and(|remainder| remainder.starts_with('='))
    })
}
'''
    if "fn root_help_requested" not in text:
        if helper_marker not in text:
            raise RuntimeError("main helper insertion point was not found")
        text = text.replace(helper_marker, "\n" + helpers + helper_marker, 1)
    path.write_text(text)


def patch_global(product: Path) -> None:
    path = product / "src/global.rs"
    text = path.read_text()
    text = replace_once(
        text,
        "use std::collections::BTreeMap;",
        "use std::collections::{BTreeMap, BTreeSet};",
        "global collection imports",
    )
    text = text.replace(
        "use clap::{Args, CommandFactory, Parser, Subcommand};",
        "use clap::{Args, Parser, Subcommand};",
        1,
    )
    text = text.replace(
        "use crate::{dev, interactive, manifestless};",
        "use crate::{interactive, manifestless};",
        1,
    )

    profile = '''#[derive(Debug, Clone)]
struct Profile {
    root: PathBuf,
    metadata: ProfileMetadata,
}
'''
    replacement = profile + '''
#[derive(Debug)]
struct ProfileReplacement {
    root: PathBuf,
    backup: Option<PathBuf>,
}
'''
    if "struct ProfileReplacement" not in text:
        text = replace_once(text, profile, replacement, "profile transaction type")

    old_help = '''fn print_root_help() -> Result<()> {
    let mut command = dev::augment_root_command(augment_root_command(crate::cli::Cli::command()));
    command.print_help().context("printing zed help")?;
    println!();
    Ok(())
}
'''
    if old_help in text:
        text = text.replace(
            old_help,
            '''fn print_root_help() -> Result<()> {
    crate::completion::print_root_help()
}
''',
            1,
        )

    start = text.index(
        "fn install(cfg: &Config, bin_dir: &Path, options: GlobalInstallArgs) -> Result<i32> {"
    )
    end = text.index("\nfn uninstall(", start)
    install = r'''fn install(cfg: &Config, bin_dir: &Path, options: GlobalInstallArgs) -> Result<i32> {
    let _lock = acquire_lock(cfg)?;
    let mut replacements = Vec::new();
    if options.frozen {
        let profiles = selected_profiles(cfg, &options.specs)?;
        if profiles.is_empty() {
            bail!("no global package profiles are installed");
        }
        for profile in &profiles {
            manifestless::install(
                &profile.root,
                cfg,
                &[],
                true,
                options.install_mode,
                Adapter::None,
                options.allow_build,
                options.target.as_deref(),
                true,
                true,
            )?;
        }
    } else {
        if options.specs.is_empty() {
            bail!(
                "global install needs one or more `org/name[@requirement]` package specs; use --frozen to restore existing profiles"
            );
        }
        let mut package_keys = BTreeSet::new();
        for requested in &options.specs {
            let (key, _) = parse_package_spec(requested)?;
            if !package_keys.insert(key.clone()) {
                bail!("global install contains duplicate top-level package `{key}`");
            }
        }

        let result: Result<()> = (|| {
            for requested in &options.specs {
                let (key, _) = parse_package_spec(requested)?;
                let root = profile_root(cfg, &key)?;
                replacements.push(stage_profile_replacement(&root)?);
                manifestless::install(
                    &root,
                    cfg,
                    std::slice::from_ref(requested),
                    false,
                    options.install_mode,
                    Adapter::None,
                    options.allow_build,
                    options.target.as_deref(),
                    true,
                    true,
                )?;
                write_metadata(
                    &root,
                    &ProfileMetadata {
                        package: key.clone(),
                        requested: requested.clone(),
                    },
                )?;
                let bin_count = profile_bins(&root)?.len();
                if bin_count == 0 {
                    eprintln!(
                        "warning: {key} currently exposes no built [bin] entries in this profile; if it declares a [build] step, reinstall with --allow-build"
                    );
                }
            }
            Ok(())
        })();
        if let Err(error) = result {
            rollback_profile_replacements(&replacements).with_context(|| {
                format!("rolling back failed global profile installation after: {error:#}")
            })?;
            return Err(error);
        }
    }

    let sync_result: Result<(Vec<Profile>, usize)> = (|| {
        let profiles = discover_profiles(cfg)?;
        let installed = sync_bins(cfg, bin_dir, &profiles)?;
        Ok((profiles, installed))
    })();
    let (profiles, installed) = match sync_result {
        Ok(result) => result,
        Err(error) => {
            rollback_profile_replacements(&replacements).with_context(|| {
                format!("rolling back global profiles after PATH synchronization failed: {error:#}")
            })?;
            return Err(error);
        }
    };
    discard_profile_backups(&replacements);
    print_path_guidance(bin_dir);
    println!(
        "{} global package profile(s); {} executable(s) managed in {}",
        profiles.len(),
        installed,
        bin_dir.display()
    );
    Ok(0)
}

fn stage_profile_replacement(root: &Path) -> Result<ProfileReplacement> {
    let backup = if root.exists() {
        let parent = root.parent().context("global profile has no parent")?;
        let name = root
            .file_name()
            .and_then(|value| value.to_str())
            .context("global profile name is not valid UTF-8")?;
        let backup = parent.join(format!(
            ".{name}.zed-backup-{}",
            uuid::Uuid::new_v4()
        ));
        fs::rename(root, &backup).with_context(|| {
            format!(
                "moving existing global profile {} to rollback backup {}",
                root.display(),
                backup.display()
            )
        })?;
        Some(backup)
    } else {
        None
    };
    if let Err(error) = fs::create_dir_all(root) {
        if let Some(backup) = &backup {
            let _ = fs::rename(backup, root);
        }
        return Err(error)
            .with_context(|| format!("creating staged global profile {}", root.display()));
    }
    Ok(ProfileReplacement {
        root: root.to_path_buf(),
        backup,
    })
}

fn rollback_profile_replacements(replacements: &[ProfileReplacement]) -> Result<()> {
    for replacement in replacements.iter().rev() {
        if replacement.root.exists() {
            fs::remove_dir_all(&replacement.root).with_context(|| {
                format!(
                    "removing failed staged global profile {}",
                    replacement.root.display()
                )
            })?;
        }
        if let Some(backup) = &replacement.backup {
            fs::rename(backup, &replacement.root).with_context(|| {
                format!(
                    "restoring global profile backup {} to {}",
                    backup.display(),
                    replacement.root.display()
                )
            })?;
        }
    }
    Ok(())
}

fn discard_profile_backups(replacements: &[ProfileReplacement]) {
    for replacement in replacements {
        let Some(backup) = &replacement.backup else {
            continue;
        };
        if let Err(error) = fs::remove_dir_all(backup)
            && error.kind() != std::io::ErrorKind::NotFound
        {
            eprintln!(
                "warning: could not remove committed global profile backup {}: {error}",
                backup.display()
            );
        }
    }
}
'''
    text = text[:start] + install + text[end:]

    old_sync = '''fn sync_bins(cfg: &Config, bin_dir: &Path, profiles: &[Profile]) -> Result<usize> {
    let desired = collect_desired_bins(profiles)?;
    let previous = load_state(cfg)?;
    fs::create_dir_all(bin_dir)?;

    for (name, managed) in &previous.bins {
'''
    new_sync = '''fn sync_bins(cfg: &Config, bin_dir: &Path, profiles: &[Profile]) -> Result<usize> {
    let desired = collect_desired_bins(profiles)?;
    let previous = load_state(cfg)?;
    fs::create_dir_all(bin_dir)?;

    // Prove every destination is absent or still owned before removing any
    // stale command. Rejected installation cannot partially mutate PATH.
    for name in desired.keys() {
        let destination = bin_dir.join(name);
        if !destination.exists() {
            continue;
        }
        let current = hash_file(&destination)?;
        let owned = previous
            .bins
            .get(name)
            .is_some_and(|managed| managed.sha256 == current);
        if !owned {
            bail!(
                "refusing to replace unmanaged global executable {}; choose another --global-bin-dir or remove the collision explicitly",
                destination.display()
            );
        }
    }

    for (name, managed) in &previous.bins {
'''
    text = replace_once(text, old_sync, new_sync, "PATH mutation preflight")
    old_duplicate = '''        if destination.exists() {
            let current = hash_file(&destination)?;
            let owned = previous
                .bins
                .get(&name)
                .is_some_and(|managed| managed.sha256 == current);
            if !owned {
                bail!(
                    "refusing to replace unmanaged global executable {}; choose another --global-bin-dir or remove the collision explicitly",
                    destination.display()
                );
            }
        }
        atomic_copy(&wanted.source, &destination)?;
'''
    text = replace_once(
        text,
        old_duplicate,
        "        atomic_copy(&wanted.source, &destination)?;\n",
        "remove duplicate PATH preflight",
    )

    test_marker = '''    #[test]
    fn explicit_global_bin_directory_wins() {
'''
    tests = r'''    #[test]
    fn profile_replacement_rollback_restores_existing_and_removes_new() {
        let home = tempfile::tempdir().unwrap();
        let cfg = config(home.path());
        let existing = profile_root(&cfg, "acme/existing").unwrap();
        fs::create_dir_all(&existing).unwrap();
        fs::write(existing.join("marker"), b"old").unwrap();
        let existing_replacement = stage_profile_replacement(&existing).unwrap();
        fs::write(existing.join("marker"), b"new").unwrap();

        let new_root = profile_root(&cfg, "acme/new").unwrap();
        let new_replacement = stage_profile_replacement(&new_root).unwrap();
        fs::write(new_root.join("marker"), b"new").unwrap();

        rollback_profile_replacements(&[existing_replacement, new_replacement]).unwrap();
        assert_eq!(fs::read(existing.join("marker")).unwrap(), b"old");
        assert!(!new_root.exists());
    }

    #[test]
    fn unmanaged_collision_is_detected_before_stale_owned_command_removal() {
        let home = tempfile::tempdir().unwrap();
        let cfg = config(home.path());
        let bin_dir = home.path().join("path-bin");
        fs::create_dir_all(&bin_dir).unwrap();

        let stale_name = destination_name("stale-tool");
        let stale_path = bin_dir.join(&stale_name);
        fs::write(&stale_path, b"stale-owned").unwrap();
        let mut previous = ManagedState::default();
        previous.bins.insert(
            stale_name,
            ManagedBin {
                package: "acme/stale".to_string(),
                sha256: hash_file(&stale_path).unwrap(),
            },
        );
        atomic_write(
            &state_path(&cfg),
            &serde_json::to_vec_pretty(&previous).unwrap(),
        )
        .unwrap();

        let desired = add_profile(home.path(), "acme/new", "new-tool", b"desired");
        let unmanaged_path = bin_dir.join(destination_name("new-tool"));
        fs::write(&unmanaged_path, b"unmanaged").unwrap();

        let error = sync_bins(&cfg, &bin_dir, &[desired]).unwrap_err();
        assert!(error.to_string().contains("unmanaged global executable"));
        assert_eq!(fs::read(stale_path).unwrap(), b"stale-owned");
        assert_eq!(fs::read(unmanaged_path).unwrap(), b"unmanaged");
    }

'''
    if "profile_replacement_rollback_restores_existing_and_removes_new" not in text:
        text = replace_once(text, test_marker, tests + test_marker, "global rollback tests")
    path.write_text(text)


def patch_ops(product: Path) -> None:
    path = product / "src/ops.rs"
    text = path.read_text()
    old = '''        for locked in &lock.packages {
            if !is_slug(&locked.org) || !is_slug(&locked.name) {
                bail!(
                    "lockfile entry `{}/{}` has an invalid identity; refusing",
                    locked.org,
                    locked.name
                );
            }
            require_sha256(&locked.sha256)?;
            let vm = reg.get_version(&locked.org, &locked.name, &locked.version)?;
            if vm.sha256 != locked.sha256 {
                bail!(
                    "registry artifact for {}@{} changed (lock {} vs registry {}); refusing",
                    locked.full_name(),
                    locked.version,
                    locked.sha256,
                    vm.sha256
                );
            }
            validate_version_metadata(&vm)?;
            resolved.insert(locked.full_name(), vm);
        }
'''
    new = '''        for locked in &lock.packages {
            if !is_slug(&locked.org) || !is_slug(&locked.name) {
                bail!(
                    "lockfile entry `{}/{}` has an invalid identity; refusing",
                    locked.org,
                    locked.name
                );
            }
            require_sha256(&locked.sha256)?;
            let vm = if store.has(&locked.sha256) {
                // The lock already carries every immutable field needed to
                // authenticate the atomically promoted store entry. Do not turn
                // an exact frozen replay into a registry-availability check.
                locked_version_metadata(locked)
            } else {
                let vm = reg.get_version(&locked.org, &locked.name, &locked.version)?;
                if vm.sha256 != locked.sha256 {
                    bail!(
                        "registry artifact for {}@{} changed (lock {} vs registry {}); refusing",
                        locked.full_name(),
                        locked.version,
                        locked.sha256,
                        vm.sha256
                    );
                }
                vm
            };
            validate_version_metadata(&vm)?;
            resolved.insert(locked.full_name(), vm);
        }
'''
    text = replace_once(text, old, new, "frozen registry loop")
    marker = '''/// Install body, called with the store lock already held. Split out so the
/// build-hook path can install `[build-dependencies]` into a staging dir
/// under the same lock without deadlocking on a re-acquire.
'''
    helper = '''fn locked_version_metadata(locked: &LockedPackage) -> VersionMetadata {
    VersionMetadata {
        org: locked.org.clone(),
        name: locked.name.clone(),
        version: locked.version.clone(),
        sha256: locked.sha256.clone(),
        size: locked.size,
        format: locked.format,
        vcs_tag: locked.vcs_tag.clone(),
        vcs_commit: locked.vcs_commit.clone(),
        // This URL is never consumed while the verified store entry exists.
        download_url: String::new(),
        published_at: "1970-01-01T00:00:00Z".to_string(),
        yanked: false,
    }
}

#[cfg(test)]
#[test]
fn frozen_local_metadata_preserves_lock_identity_and_provenance() {
    let locked = LockedPackage {
        org: "acme".to_string(),
        name: "tool".to_string(),
        version: "1.2.3".to_string(),
        sha256: "a".repeat(64),
        size: 42,
        format: zed_interfaces::artifact::ArtifactFormat::TarGz,
        vcs_tag: "v1.2.3".to_string(),
        vcs_commit: Some("b".repeat(40)),
        source: "https://registry.invalid".to_string(),
    };
    let metadata = locked_version_metadata(&locked);
    assert_eq!(metadata.org, locked.org);
    assert_eq!(metadata.name, locked.name);
    assert_eq!(metadata.version, locked.version);
    assert_eq!(metadata.sha256, locked.sha256);
    assert_eq!(metadata.size, locked.size);
    assert_eq!(metadata.format, locked.format);
    assert_eq!(metadata.vcs_tag, locked.vcs_tag);
    assert_eq!(metadata.vcs_commit, locked.vcs_commit);
    assert!(metadata.download_url.is_empty());
    assert!(!metadata.yanked);
}

'''
    if "fn locked_version_metadata" not in text:
        text = replace_once(text, marker, helper + marker, "locked metadata helper")
    path.write_text(text)


def changed_files(product: Path) -> set[str]:
    output = subprocess.check_output(
        ["git", "diff", "--name-only", "-z"], cwd=product
    )
    return {
        item.decode()
        for item in output.split(b"\0")
        if item
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product", type=Path, required=True)
    parser.add_argument("--feature", type=Path, required=True)
    args = parser.parse_args()

    product = args.product.resolve()
    feature = args.feature.resolve()
    copy_feature_files(product, feature)
    patch_lib(product)
    patch_completion(product)
    patch_main(product)
    patch_global(product)
    patch_ops(product)

    # Temporary write-enabled helpers are intentionally never copied into the
    # assembled product tree.
    for name in (
        "enable-offline-frozen-global-once.yml",
        "harden-global-package-transaction-once.yml",
        "rebase-global-packages-current-main.yml",
    ):
        candidate = product / ".github/workflows" / name
        if candidate.exists():
            candidate.unlink()

    subprocess.run(["cargo", "fmt", "--all"], cwd=product, check=True)
    actual = changed_files(product)
    unexpected = actual - EXPECTED_CHANGED_FILES
    missing = {
        ".github/workflows/global-packages.yml",
        ".zpkg.lock",
        ".zpkg.toml",
        "docs/global-packages.md",
        "src/global.rs",
        "tests/global_cli.rs",
        "src/ops.rs",
    } - actual
    if unexpected:
        raise SystemExit(
            "materialization changed files outside the reviewed boundary: "
            + ", ".join(sorted(unexpected))
        )
    if missing:
        raise SystemExit(
            "materialization failed to produce required files: "
            + ", ".join(sorted(missing))
        )
    print("materialized files:")
    for name in sorted(actual):
        print(name)


if __name__ == "__main__":
    main()
