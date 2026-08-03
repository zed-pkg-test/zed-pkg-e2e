use std::collections::BTreeMap;
use std::env;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::os::unix::fs::PermissionsExt;
use std::path::{Component, Path, PathBuf};

use flate2::{Compression, GzBuilder};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use zed_cli::nix_export_bundle::{RenderedNixExportBundle, render_nix_export_bundle};
use zed_cli::nix_export_plan::{
    NIX_EXPORT_PLAN_SCHEMA_V1, NixExportPlan, PlannedDependency, PlannedPackageClass,
    PlannedZedArtifact, ResolvedNixIntent,
};
use zed_interfaces::{
    ArtifactFormat, NixBuilderNetwork, NixExportMode, NixInteropArtifact, NixPackageIdentity,
    NixPolicyEvidence, NixPolicyProfile,
};

fn sha256(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn executable_artifact(mode: u32) -> Vec<u8> {
    let encoder = GzBuilder::new()
        .mtime(0)
        .write(Vec::new(), Compression::default());
    let mut builder = tar::Builder::new(encoder);
    let payload = b"#!/bin/sh\nprintf 'external-bundle-ok\\n'\n";
    let mut header = tar::Header::new_gnu();
    header.set_size(payload.len() as u64);
    header.set_mode(mode);
    header.set_uid(0);
    header.set_gid(0);
    header.set_mtime(0);
    header.set_cksum();
    builder
        .append_data(&mut header, "package/bin/sample", payload.as_slice())
        .unwrap();
    builder.into_inner().unwrap().finish().unwrap()
}

fn symlink_artifact() -> Vec<u8> {
    let encoder = GzBuilder::new()
        .mtime(0)
        .write(Vec::new(), Compression::default());
    let mut builder = tar::Builder::new(encoder);
    let mut header = tar::Header::new_gnu();
    header.set_entry_type(tar::EntryType::Symlink);
    header.set_size(0);
    header.set_mode(0o777);
    header.set_uid(0);
    header.set_gid(0);
    header.set_mtime(0);
    header.set_link_name("/etc/passwd").unwrap();
    header.set_cksum();
    builder
        .append_data(&mut header, "package/bin/sample", std::io::empty())
        .unwrap();
    builder.into_inner().unwrap().finish().unwrap()
}

fn flake_lock() -> Vec<u8> {
    serde_json::to_vec(&json!({
        "nodes": {
            "nixpkgs": {
                "locked": {
                    "lastModified": 1782467914_u64,
                    "narHash": "sha256-pGvFkM8N0xEkIIXDe5YYfbEAvHrk4IxBrjB/x8OomhE=",
                    "owner": "NixOS",
                    "repo": "nixpkgs",
                    "rev": "e73de5be04e0eff4190a1432b946d469c794e7b4",
                    "type": "github"
                },
                "original": {
                    "owner": "NixOS",
                    "repo": "nixpkgs",
                    "rev": "e73de5be04e0eff4190a1432b946d469c794e7b4",
                    "type": "github"
                }
            },
            "root": { "inputs": { "nixpkgs": "nixpkgs" } }
        },
        "root": "root",
        "version": 7
    }))
    .unwrap()
}

fn plan(artifact: &[u8], system: &str) -> NixExportPlan {
    NixExportPlan {
        schema: NIX_EXPORT_PLAN_SCHEMA_V1,
        package: NixPackageIdentity {
            org: "example".into(),
            name: "sample".into(),
            version: "1.2.3".into(),
            target: None,
        },
        package_class: PlannedPackageClass::PrebuiltBin,
        intent: ResolvedNixIntent {
            mode: NixExportMode::Artifact,
            attribute: "sample".into(),
            systems: vec![system.into()],
            outputs: vec!["out".into()],
        },
        source: PlannedZedArtifact {
            file_name: "example-sample-1.2.3.tar.gz".into(),
            artifact: NixInteropArtifact {
                format: ArtifactFormat::TarGz,
                sha256: sha256(artifact),
                size: artifact.len() as u64,
            },
            manifest_sha256: "1".repeat(64),
            lock_sha256: "2".repeat(64),
        },
        bins: BTreeMap::from([("sample".into(), "bin/sample".into())]),
        dependencies: Vec::new(),
        policy: NixPolicyEvidence {
            profile: NixPolicyProfile::StrictV1,
            pure_evaluation: true,
            import_from_derivation: false,
            sandbox_required: true,
            builder_network: NixBuilderNetwork::Disabled,
            dirty_source: false,
            publishable: true,
        },
    }
}

fn required_path(name: &str) -> PathBuf {
    PathBuf::from(env::var_os(name).unwrap_or_else(|| panic!("{name} must be set")))
}

fn write_bundle(root: &Path, rendered: &RenderedNixExportBundle) {
    assert!(
        !root.exists(),
        "bundle output must be fresh: {}",
        root.display()
    );
    fs::create_dir(root).unwrap();
    fs::set_permissions(root, fs::Permissions::from_mode(0o755)).unwrap();

    for (relative, bytes) in &rendered.files {
        let path = Path::new(relative);
        assert!(
            path.components()
                .all(|component| matches!(component, Component::Normal(_))),
            "renderer returned unsafe path: {relative}"
        );
        let destination = root.join(path);
        fs::create_dir_all(destination.parent().unwrap()).unwrap();
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&destination)
            .unwrap();
        file.write_all(bytes).unwrap();
        file.sync_all().unwrap();
        fs::set_permissions(&destination, fs::Permissions::from_mode(0o644)).unwrap();
    }
}

fn assert_source_redacted(rendered: &RenderedNixExportBundle) {
    let canary = env::var("FORBIDDEN_CANARY").expect("FORBIDDEN_CANARY must be set");
    let path = env::var("FORBIDDEN_PATH").expect("FORBIDDEN_PATH must be set");
    for bytes in rendered.files.values() {
        let text = String::from_utf8_lossy(bytes);
        assert!(!text.contains(&canary), "bundle retained credential canary");
        assert!(!text.contains(&path), "bundle retained absolute work path");
    }
}

#[test]
fn public_renderer_replays_byte_identically_and_detects_tampering() {
    let system = env::var("EXTERNAL_NIX_SYSTEM").expect("EXTERNAL_NIX_SYSTEM must be set");
    let artifact = executable_artifact(0o755);
    let lock = flake_lock();
    let plan = plan(&artifact, &system);

    let first = render_nix_export_bundle(&plan, &artifact, &lock).unwrap();
    let second = render_nix_export_bundle(&plan, &artifact, &lock).unwrap();
    assert_eq!(first, second);
    first.validate().unwrap();
    second.validate().unwrap();
    assert_source_redacted(&first);

    write_bundle(&required_path("BUNDLE_OUT_A"), &first);
    write_bundle(&required_path("BUNDLE_OUT_B"), &second);

    let report_path = required_path("BUNDLE_REPORT");
    fs::create_dir_all(report_path.parent().unwrap()).unwrap();
    let report = json!({
        "schema": "zed-pkg-test.nix-flake-bundle-canary/v1",
        "candidate": env::var("CANDIDATE_SHA").unwrap_or_default(),
        "system": system,
        "artifact_sha256": plan.source.artifact.sha256,
        "artifact_size": plan.source.artifact.size,
        "bundle_sha256": first.inventory.bundle_sha256,
        "plan_sha256": first.inventory.plan_sha256,
        "flake_lock_sha256": first.inventory.flake_lock_sha256,
        "file_count": first.files.len(),
        "credential_canaries_retained": false,
        "external_registry_required": false
    });
    fs::write(report_path, serde_json::to_vec(&report).unwrap()).unwrap();

    let mut changed_package = first.clone();
    changed_package
        .files
        .get_mut("package.nix")
        .unwrap()
        .extend_from_slice(b"\n# tampered\n");
    assert!(changed_package.validate().is_err());

    let mut missing_artifact = first.clone();
    missing_artifact
        .files
        .remove("artifacts/example-sample-1.2.3.tar.gz");
    assert!(missing_artifact.validate().is_err());

    let mut extra_file = first.clone();
    extra_file
        .files
        .insert("unexpected.txt".into(), b"unexpected".to_vec());
    assert!(extra_file.validate().is_err());

    let mut changed_lock = first.clone();
    changed_lock
        .files
        .get_mut("flake.lock")
        .unwrap()
        .push(b'\n');
    assert!(changed_lock.validate().is_err());

    let mut changed_inventory = first.clone();
    changed_inventory
        .files
        .get_mut("metadata/bundle.json")
        .unwrap()
        .push(b'\n');
    assert!(changed_inventory.validate().is_err());
}

#[test]
fn wrong_artifact_digest_fails_before_bundle_creation() {
    let artifact = executable_artifact(0o755);
    let mut plan = plan(&artifact, "x86_64-linux");
    plan.source.artifact.sha256 = "f".repeat(64);
    assert!(render_nix_export_bundle(&plan, &artifact, &flake_lock()).is_err());
}

#[test]
fn mutable_nixpkgs_revision_fails_closed() {
    let artifact = executable_artifact(0o755);
    let plan = plan(&artifact, "x86_64-linux");
    let mut lock: Value = serde_json::from_slice(&flake_lock()).unwrap();
    lock["nodes"]["nixpkgs"]["locked"]["rev"] = json!("nixos-unstable");
    assert!(
        render_nix_export_bundle(&plan, &artifact, &serde_json::to_vec(&lock).unwrap()).is_err()
    );
}

#[test]
fn archive_links_fail_closed() {
    let artifact = symlink_artifact();
    let plan = plan(&artifact, "x86_64-linux");
    assert!(render_nix_export_bundle(&plan, &artifact, &flake_lock()).is_err());
}

#[test]
fn non_executable_declared_binary_fails_closed() {
    let artifact = executable_artifact(0o644);
    let plan = plan(&artifact, "x86_64-linux");
    assert!(render_nix_export_bundle(&plan, &artifact, &flake_lock()).is_err());
}

#[test]
fn dependency_edges_remain_outside_strict_bundle_v1() {
    let artifact = executable_artifact(0o755);
    let mut plan = plan(&artifact, "x86_64-linux");
    plan.dependencies.push(PlannedDependency {
        org: "example".into(),
        name: "dependency".into(),
        version: "1.0.0".into(),
        sha256: "3".repeat(64),
    });
    assert!(render_nix_export_bundle(&plan, &artifact, &flake_lock()).is_err());
}
