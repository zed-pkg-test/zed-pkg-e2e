use zed_interfaces::{
    ArtifactFormat, NATIVE_REGISTRY_ADAPTER_SCHEMA_V1, NativeArtifact, NativePackageIdentity,
    NativePlatform, NativePlatformPackage, NativePublication, NativePublicationKind,
    NativeRegistry, NativeRegistryAdapterRecord, NativeRegistryError, ZedNativePackageIdentity,
    native_versions_collide, semver_precedence_identity,
};

const VERSION: &str = "1.4.2";
const WRAPPER: &str = "@fiducia/core";
const LINUX_PACKAGE: &str = "@fiducia/core-linux-arm64-musl";
const DARWIN_PACKAGE: &str = "@fiducia/core-darwin-arm64";

fn platform(os: &str, arch: &str, libc: Option<&str>) -> NativePlatform {
    NativePlatform {
        os: os.to_string(),
        arch: arch.to_string(),
        libc: libc.map(str::to_string),
    }
}

fn artifact(digit: char) -> NativeArtifact {
    NativeArtifact {
        sha256: std::iter::repeat_n(digit, 64).collect(),
        size: 1024,
        format: ArtifactFormat::TarGz,
    }
}

fn package(name: &str, kind: NativePublicationKind, digest_digit: char) -> NativePublication {
    NativePublication {
        package: NativePackageIdentity {
            name: name.to_string(),
            version: VERSION.to_string(),
        },
        kind,
        platform: None,
        platform_packages: Vec::new(),
        artifact: artifact(digest_digit),
    }
}

fn valid_npm_record() -> NativeRegistryAdapterRecord {
    let linux_arm64_musl = platform("linux", "arm64", Some("musl"));
    let darwin_arm64 = platform("darwin", "arm64", None);

    let mut wrapper = package(WRAPPER, NativePublicationKind::Meta, 'a');
    wrapper.platform_packages = vec![
        NativePlatformPackage {
            platform: linux_arm64_musl.clone(),
            package: LINUX_PACKAGE.to_string(),
        },
        NativePlatformPackage {
            platform: darwin_arm64.clone(),
            package: DARWIN_PACKAGE.to_string(),
        },
    ];

    let mut linux = package(LINUX_PACKAGE, NativePublicationKind::Platform, 'b');
    linux.platform = Some(linux_arm64_musl);

    let mut darwin = package(DARWIN_PACKAGE, NativePublicationKind::Platform, 'c');
    darwin.platform = Some(darwin_arm64);

    NativeRegistryAdapterRecord {
        schema: NATIVE_REGISTRY_ADAPTER_SCHEMA_V1.to_string(),
        registry: NativeRegistry::Npm,
        source: ZedNativePackageIdentity {
            org: "fiducia".to_string(),
            name: "core".to_string(),
            version: VERSION.to_string(),
            target: Some("node".to_string()),
        },
        publications: vec![darwin, wrapper, linux],
    }
}

fn meta_mut(record: &mut NativeRegistryAdapterRecord) -> &mut NativePublication {
    record
        .publications
        .iter_mut()
        .find(|publication| publication.kind == NativePublicationKind::Meta)
        .expect("fixture contains one meta publication")
}

fn platform_mut(
    record: &mut NativeRegistryAdapterRecord,
    package_name: &str,
) -> &mut NativePublication {
    record
        .publications
        .iter_mut()
        .find(|publication| publication.package.name == package_name)
        .expect("fixture contains requested platform publication")
}

fn assert_positive_contract() {
    let record = valid_npm_record();
    record.validate().expect("valid npm publication family");

    let canonical = record
        .canonical_json_bytes()
        .expect("canonical native-registry adapter JSON");
    assert!(!canonical.is_empty());

    let mut reordered = record.clone();
    reordered.publications.reverse();
    for publication in &mut reordered.publications {
        publication.platform_packages.reverse();
    }
    assert_eq!(
        canonical,
        reordered
            .canonical_json_bytes()
            .expect("presentation order must not affect canonical bytes")
    );

    assert_eq!(
        semver_precedence_identity("1.4.2+linux-arm64").unwrap(),
        VERSION
    );
    assert!(native_versions_collide("1.4.2+linux", "1.4.2+darwin").unwrap());
    assert!(!native_versions_collide("1.4.2-rc.1", VERSION).unwrap());

    let cargo_record = NativeRegistryAdapterRecord {
        schema: NATIVE_REGISTRY_ADAPTER_SCHEMA_V1.to_string(),
        registry: NativeRegistry::Cargo,
        source: ZedNativePackageIdentity {
            org: "fiducia".to_string(),
            name: "core".to_string(),
            version: VERSION.to_string(),
            target: Some("rust".to_string()),
        },
        publications: vec![package(
            "fiducia_core",
            NativePublicationKind::Portable,
            'd',
        )],
    };
    cargo_record
        .validate()
        .expect("portable Cargo publication must validate");
}

fn assert_identity_rejections() {
    let mut unsupported_schema = valid_npm_record();
    unsupported_schema.schema = "zed.native-registry-adapter/v2".to_string();
    assert!(matches!(
        unsupported_schema.validate(),
        Err(NativeRegistryError::UnsupportedSchema { .. })
    ));

    let mut no_publications = valid_npm_record();
    no_publications.publications.clear();
    assert!(matches!(
        no_publications.validate(),
        Err(NativeRegistryError::NoPublications)
    ));

    let mut invalid_semver = valid_npm_record();
    invalid_semver.source.version = "1.4".to_string();
    assert!(matches!(
        invalid_semver.validate(),
        Err(NativeRegistryError::InvalidSemver { .. })
    ));

    let mut build_metadata = valid_npm_record();
    build_metadata.source.version = "1.4.2+linux".to_string();
    for publication in &mut build_metadata.publications {
        publication.package.version = "1.4.2+linux".to_string();
    }
    assert!(matches!(
        build_metadata.validate(),
        Err(NativeRegistryError::BuildMetadataNotAllowed { .. })
    ));

    let mut version_drift = valid_npm_record();
    version_drift.publications[0].package.version = "1.4.3".to_string();
    assert!(matches!(
        version_drift.validate(),
        Err(NativeRegistryError::VersionDrift { .. })
    ));

    let mut invalid_npm_name = valid_npm_record();
    invalid_npm_name.publications[0].package.name = "@Fiducia/core".to_string();
    assert!(matches!(
        invalid_npm_name.validate(),
        Err(NativeRegistryError::InvalidPackageName {
            registry: NativeRegistry::Npm,
            ..
        })
    ));

    let mut invalid_cargo_name = NativeRegistryAdapterRecord {
        schema: NATIVE_REGISTRY_ADAPTER_SCHEMA_V1.to_string(),
        registry: NativeRegistry::Cargo,
        source: ZedNativePackageIdentity {
            org: "fiducia".to_string(),
            name: "core".to_string(),
            version: VERSION.to_string(),
            target: Some("rust".to_string()),
        },
        publications: vec![package(
            "bad/name",
            NativePublicationKind::Portable,
            'd',
        )],
    };
    assert!(matches!(
        invalid_cargo_name.validate(),
        Err(NativeRegistryError::InvalidPackageName {
            registry: NativeRegistry::Cargo,
            ..
        })
    ));
}

fn assert_platform_rejections() {
    let mut missing_selector = valid_npm_record();
    platform_mut(&mut missing_selector, LINUX_PACKAGE).platform = None;
    assert!(matches!(
        missing_selector.validate(),
        Err(NativeRegistryError::PlatformRequired { .. })
    ));

    let mut unexpected_selector = valid_npm_record();
    meta_mut(&mut unexpected_selector).platform = Some(platform("linux", "x64", Some("gnu")));
    assert!(matches!(
        unexpected_selector.validate(),
        Err(NativeRegistryError::UnexpectedPlatform { .. })
    ));

    let mut invalid_platform_token = valid_npm_record();
    platform_mut(&mut invalid_platform_token, LINUX_PACKAGE)
        .platform
        .as_mut()
        .expect("platform selector")
        .arch = "ARM64".to_string();
    assert!(matches!(
        invalid_platform_token.validate(),
        Err(NativeRegistryError::InvalidPlatformToken { .. })
    ));

    let mut duplicate_meta_edge = valid_npm_record();
    let duplicate = meta_mut(&mut duplicate_meta_edge).platform_packages[0].clone();
    meta_mut(&mut duplicate_meta_edge)
        .platform_packages
        .push(duplicate);
    assert!(matches!(
        duplicate_meta_edge.validate(),
        Err(NativeRegistryError::DuplicateMetaPlatform { .. })
    ));

    let mut mismatched_edge = valid_npm_record();
    meta_mut(&mut mismatched_edge).platform_packages[0].package = DARWIN_PACKAGE.to_string();
    assert!(matches!(
        mismatched_edge.validate(),
        Err(NativeRegistryError::PlatformPackageMismatch { .. })
    ));

    let mut dangling_edge = valid_npm_record();
    dangling_edge
        .publications
        .retain(|publication| publication.package.name != LINUX_PACKAGE);
    assert!(matches!(
        dangling_edge.validate(),
        Err(NativeRegistryError::MissingPlatformPublication { .. })
    ));

    let mut duplicate_platform = valid_npm_record();
    let linux_platform = platform_mut(&mut duplicate_platform, LINUX_PACKAGE)
        .platform
        .clone();
    let mut extra = package(
        "@fiducia/core-linux-arm64-alias",
        NativePublicationKind::Platform,
        'd',
    );
    extra.platform = linux_platform;
    duplicate_platform.publications.push(extra);
    assert!(matches!(
        duplicate_platform.validate(),
        Err(NativeRegistryError::DuplicatePlatformPublication { .. })
    ));

    let mut multiple_meta = valid_npm_record();
    multiple_meta.publications.push(package(
        "@fiducia/core-wrapper",
        NativePublicationKind::Meta,
        'd',
    ));
    assert!(matches!(
        multiple_meta.validate(),
        Err(NativeRegistryError::MultipleMetaPackages)
    ));

    let mut platform_selects_platform = valid_npm_record();
    let selection = meta_mut(&mut platform_selects_platform).platform_packages[0].clone();
    platform_mut(&mut platform_selects_platform, LINUX_PACKAGE)
        .platform_packages
        .push(selection);
    assert!(matches!(
        platform_selects_platform.validate(),
        Err(NativeRegistryError::PlatformPackagesNotAllowed { .. })
    ));
}

fn assert_artifact_and_duplicate_rejections() {
    let mut malformed_digest = valid_npm_record();
    malformed_digest.publications[0].artifact.sha256 = "A".repeat(64);
    assert!(matches!(
        malformed_digest.validate(),
        Err(NativeRegistryError::InvalidSha256 { .. })
    ));

    let mut empty_artifact = valid_npm_record();
    empty_artifact.publications[0].artifact.size = 0;
    assert!(matches!(
        empty_artifact.validate(),
        Err(NativeRegistryError::EmptyArtifact { .. })
    ));

    let mut duplicate_package = valid_npm_record();
    duplicate_package
        .publications
        .push(duplicate_package.publications[0].clone());
    assert!(matches!(
        duplicate_package.validate(),
        Err(NativeRegistryError::DuplicatePackageVersion { .. })
    ));
}

fn main() {
    assert_positive_contract();
    assert_identity_rejections();
    assert_platform_rejections();
    assert_artifact_and_duplicate_rejections();
}
