use zed_interfaces::{
    ArtifactFormat, NATIVE_DEPENDENCY_LOCK_SCHEMA_V1, NativeArtifact, NativeDependencyError,
    NativeDependencyLock, NativeRegistry, NativeVersionCandidate, NativeVersionRequirement,
};

fn artifact(digit: char) -> NativeArtifact {
    NativeArtifact {
        sha256: std::iter::repeat_n(digit, 64).collect(),
        size: 256,
        format: ArtifactFormat::TarGz,
    }
}

fn candidate(version: &str, digit: char) -> NativeVersionCandidate {
    NativeVersionCandidate {
        version: version.to_string(),
        artifact: artifact(digit),
    }
}

fn assert_source_aware_translation() {
    let npm_exact = NativeVersionRequirement::parse(NativeRegistry::Npm, "1.2.3").unwrap();
    let cargo_caret =
        NativeVersionRequirement::parse(NativeRegistry::Cargo, "1.2.3").unwrap();
    assert_eq!(npm_exact.canonical, "=1.2.3");
    assert_eq!(cargo_caret.canonical, "^1.2.3");
    assert!(npm_exact.matches("1.2.3").unwrap());
    assert!(!npm_exact.matches("1.2.4").unwrap());
    assert!(cargo_caret.matches("1.9.9").unwrap());
    assert!(!cargo_caret.matches("2.0.0").unwrap());

    let npm_minor = NativeVersionRequirement::parse(NativeRegistry::Npm, "1.2").unwrap();
    let cargo_minor = NativeVersionRequirement::parse(NativeRegistry::Cargo, "1.2").unwrap();
    assert_eq!(npm_minor.canonical, "1.2.*");
    assert_eq!(cargo_minor.canonical, "^1.2");
    assert!(!npm_minor.matches("1.3.0").unwrap());
    assert!(cargo_minor.matches("1.3.0").unwrap());

    let npm_comparators =
        NativeVersionRequirement::parse(NativeRegistry::Npm, ">=1.2.3 <2.0.0").unwrap();
    let cargo_comparators =
        NativeVersionRequirement::parse(NativeRegistry::Cargo, ">=1.2.3, <2.0.0").unwrap();
    assert_eq!(npm_comparators.canonical, ">=1.2.3, <2.0.0");
    assert_eq!(cargo_comparators.canonical, ">=1.2.3, <2.0.0");

    let npm_spaced =
        NativeVersionRequirement::parse(NativeRegistry::Npm, ">= 1.2.3 < 2.0.0").unwrap();
    assert_eq!(npm_spaced.canonical, ">=1.2.3, <2.0.0");
    assert!(npm_spaced.matches("1.9.9").unwrap());
    assert!(!npm_spaced.matches("2.0.0").unwrap());

    let cargo_spaced =
        NativeVersionRequirement::parse(NativeRegistry::Cargo, ">= 1.2, < 1.5").unwrap();
    assert_eq!(cargo_spaced.canonical, ">=1.2, <1.5");
    assert!(cargo_spaced.matches("1.4.99").unwrap());
    assert!(!cargo_spaced.matches("1.5.0").unwrap());

    let npm_x = NativeVersionRequirement::parse(NativeRegistry::Npm, "v1.2.X").unwrap();
    assert_eq!(npm_x.canonical, "1.2.*");

    let npm_gt_major = NativeVersionRequirement::parse(NativeRegistry::Npm, ">1").unwrap();
    let npm_gt_minor = NativeVersionRequirement::parse(NativeRegistry::Npm, ">1.2").unwrap();
    let npm_lte_minor = NativeVersionRequirement::parse(NativeRegistry::Npm, "<=1.2").unwrap();
    assert_eq!(npm_gt_major.canonical, ">=2.0.0");
    assert_eq!(npm_gt_minor.canonical, ">=1.3.0");
    assert_eq!(npm_lte_minor.canonical, "<1.3.0");
    assert!(!npm_gt_major.matches("1.99.99").unwrap());
    assert!(npm_gt_major.matches("2.0.0").unwrap());
    assert!(!npm_gt_minor.matches("1.2.999").unwrap());
    assert!(npm_gt_minor.matches("1.3.0").unwrap());
    assert!(npm_lte_minor.matches("1.2.999").unwrap());
    assert!(!npm_lte_minor.matches("1.3.0").unwrap());

    let cargo_zero = NativeVersionRequirement::parse(NativeRegistry::Cargo, "0.2.3").unwrap();
    assert!(cargo_zero.matches("0.2.9").unwrap());
    assert!(!cargo_zero.matches("0.3.0").unwrap());

    let ordinary = NativeVersionRequirement::parse(NativeRegistry::Npm, "^1.2.3").unwrap();
    assert!(!ordinary.matches("1.3.0-beta.1").unwrap());
    let prerelease =
        NativeVersionRequirement::parse(NativeRegistry::Npm, "1.3.0-beta.1").unwrap();
    assert!(prerelease.matches("1.3.0-beta.1").unwrap());
}

fn assert_exact_lock_resolution() {
    let candidates = vec![
        candidate("1.2.3", 'a'),
        candidate("1.9.0", 'b'),
        candidate("1.10.0", 'c'),
        candidate("2.0.0", 'd'),
    ];
    let cargo = NativeDependencyLock::resolve(
        NativeRegistry::Cargo,
        "fiducia_core",
        "1.2.3",
        &candidates,
    )
    .unwrap();
    assert_eq!(cargo.schema, NATIVE_DEPENDENCY_LOCK_SCHEMA_V1);
    assert_eq!(cargo.package.version, "1.10.0");
    assert_eq!(cargo.artifact.sha256, "c".repeat(64));
    cargo.validate().unwrap();
    assert!(!cargo.canonical_json_bytes().unwrap().is_empty());

    let mut reversed = candidates;
    reversed.reverse();
    let reordered = NativeDependencyLock::resolve(
        NativeRegistry::Cargo,
        "fiducia_core",
        "1.2.3",
        &reversed,
    )
    .unwrap();
    assert_eq!(cargo, reordered);
    assert_eq!(
        cargo.canonical_json_bytes().unwrap(),
        reordered.canonical_json_bytes().unwrap()
    );

    let npm = NativeDependencyLock::resolve(
        NativeRegistry::Npm,
        "@fiducia/core",
        "1.2.3",
        &[candidate("1.2.3", 'a'), candidate("1.2.4", 'b')],
    )
    .unwrap();
    assert_eq!(npm.requirement.canonical, "=1.2.3");
    assert_eq!(npm.package.version, "1.2.3");
}

fn assert_requirement_rejections() {
    for declared in [
        "",
        " latest",
        "latest",
        "1.0.0 || 2.0.0",
        "1.0.0 - 2.0.0",
        "workspace:^1.0.0",
        "file:../core",
        "npm:@fiducia/core@1.0.0",
        "git+https://example.invalid/core",
        ">=1.0.0, <2.0.0",
        "1.2.3+linux",
        "01.2",
        "1.02",
        ">1.02",
        "^01.2",
        "9007199254740992.0.0",
        ">9007199254740991",
    ] {
        assert!(NativeVersionRequirement::parse(NativeRegistry::Npm, declared).is_err());
    }

    for declared in [
        "",
        "1.0.0 || 2.0.0",
        "1.0.0 - 2.0.0",
        ">=1.0.0 <2.0.0",
        ">= 1.0 < 2.0",
        "1.x",
        "file:../core",
        "1.2.3+linux",
    ] {
        assert!(NativeVersionRequirement::parse(NativeRegistry::Cargo, declared).is_err());
    }
}

fn assert_lock_rejections() {
    let mut canonical_drift = NativeDependencyLock::resolve(
        NativeRegistry::Npm,
        "@fiducia/core",
        "^1.2.3",
        &[candidate("1.9.0", 'a')],
    )
    .unwrap();
    canonical_drift.requirement.canonical = "^1.3.0".to_string();
    assert!(matches!(
        canonical_drift.validate(),
        Err(NativeDependencyError::CanonicalRequirementDrift { .. })
    ));

    let mut version_drift = NativeDependencyLock::resolve(
        NativeRegistry::Npm,
        "@fiducia/core",
        "^1.2.3",
        &[candidate("1.9.0", 'a')],
    )
    .unwrap();
    version_drift.package.version = "2.0.0".to_string();
    assert!(matches!(
        version_drift.validate(),
        Err(NativeDependencyError::ResolvedVersionDoesNotMatch { .. })
    ));

    let mut malformed_digest = NativeDependencyLock::resolve(
        NativeRegistry::Npm,
        "@fiducia/core",
        "^1.2.3",
        &[candidate("1.9.0", 'a')],
    )
    .unwrap();
    malformed_digest.artifact.sha256 = "A".repeat(64);
    assert!(matches!(
        malformed_digest.validate(),
        Err(NativeDependencyError::InvalidSha256 { .. })
    ));

    let mut zero_digest = NativeDependencyLock::resolve(
        NativeRegistry::Npm,
        "@fiducia/core",
        "^1.2.3",
        &[candidate("1.9.0", 'a')],
    )
    .unwrap();
    zero_digest.artifact.sha256 = "0".repeat(64);
    assert!(matches!(
        zero_digest.validate(),
        Err(NativeDependencyError::InvalidSha256 { .. })
    ));

    let mut empty_artifact = NativeDependencyLock::resolve(
        NativeRegistry::Npm,
        "@fiducia/core",
        "^1.2.3",
        &[candidate("1.9.0", 'a')],
    )
    .unwrap();
    empty_artifact.artifact.size = 0;
    assert!(matches!(
        empty_artifact.validate(),
        Err(NativeDependencyError::EmptyArtifact { .. })
    ));

    assert!(matches!(
        NativeDependencyLock::resolve(
            NativeRegistry::Cargo,
            "fiducia_core",
            "1.2.3",
            &[candidate("1.2.3", 'a'), candidate("1.2.3", 'b')],
        ),
        Err(NativeDependencyError::DuplicateCandidateVersion { .. })
    ));

    assert!(matches!(
        NativeDependencyLock::resolve(
            NativeRegistry::Cargo,
            "fiducia_core",
            "1.2.3",
            &[candidate("1.2.3+linux", 'a')],
        ),
        Err(NativeDependencyError::BuildMetadataNotAllowed { .. })
    ));

    assert!(matches!(
        NativeDependencyLock::resolve(
            NativeRegistry::Cargo,
            "fiducia_core",
            "^2.0.0",
            &[candidate("1.9.0", 'a')],
        ),
        Err(NativeDependencyError::NoMatchingVersion { .. })
    ));

    assert!(matches!(
        NativeDependencyLock::resolve(
            NativeRegistry::Npm,
            "@Fiducia/core",
            "1.2.3",
            &[candidate("1.2.3", 'a')],
        ),
        Err(NativeDependencyError::InvalidPackageName { .. })
    ));

    assert!(matches!(
        NativeDependencyLock::resolve(
            NativeRegistry::Npm,
            "@fiducia/core",
            "*",
            &[candidate("9007199254740992.0.0", 'a')],
        ),
        Err(NativeDependencyError::InvalidVersion { .. })
    ));
}

fn main() {
    assert_source_aware_translation();
    assert_exact_lock_resolution();
    assert_requirement_rejections();
    assert_lock_rejections();
}
