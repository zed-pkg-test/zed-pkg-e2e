use std::collections::BTreeSet;

use serde::Deserialize;
use zed_interfaces::{
    ArtifactFormat, NATIVE_REGISTRY_ADAPTER_SCHEMA_V1, NativeArtifact, NativePackageIdentity,
    NativePlatform, NativePlatformPackage, NativePublication, NativePublicationKind,
    NativeRegistry, NativeRegistryAdapterRecord, NativeRegistryError, ZedNativePackageIdentity,
    native_versions_collide, semver_precedence_identity,
};

const INTERFACE_COMMIT: &str = "032b52f6e335e5696cb793d6a955c8f0658a95eb";
const VERSION: &str = "1.4.2";
const WRAPPER: &str = "@fiducia/core";
const LINUX_PACKAGE: &str = "@fiducia/core-linux-arm64-musl";
const DARWIN_PACKAGE: &str = "@fiducia/core-darwin-arm64";

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Corpus {
    schema: String,
    interface_commit: String,
    precedence: Vec<PrecedenceCase>,
    collisions: Vec<CollisionCase>,
    valid_records: Vec<ValidRecordCase>,
    invalid_records: Vec<InvalidRecordCase>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct PrecedenceCase {
    id: String,
    input: String,
    expected: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CollisionCase {
    id: String,
    left: String,
    right: String,
    expected: bool,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ValidRecordCase {
    id: String,
    kind: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct InvalidRecordCase {
    id: String,
    base: String,
    mutation: String,
    expected_error: String,
}

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

fn publication(
    name: &str,
    kind: NativePublicationKind,
    selected_platform: Option<NativePlatform>,
    digest_digit: char,
) -> NativePublication {
    NativePublication {
        package: NativePackageIdentity {
            name: name.to_string(),
            version: VERSION.to_string(),
        },
        kind,
        platform: selected_platform,
        platform_packages: Vec::new(),
        artifact: artifact(digest_digit),
    }
}

fn npm_multiplatform_record() -> NativeRegistryAdapterRecord {
    let linux = platform("linux", "arm64", Some("musl"));
    let darwin = platform("darwin", "arm64", None);

    let mut wrapper = publication(WRAPPER, NativePublicationKind::Meta, None, 'a');
    wrapper.platform_packages = vec![
        NativePlatformPackage {
            platform: linux.clone(),
            package: LINUX_PACKAGE.to_string(),
        },
        NativePlatformPackage {
            platform: darwin.clone(),
            package: DARWIN_PACKAGE.to_string(),
        },
    ];

    NativeRegistryAdapterRecord {
        schema: NATIVE_REGISTRY_ADAPTER_SCHEMA_V1.to_string(),
        registry: NativeRegistry::Npm,
        source: ZedNativePackageIdentity {
            org: "fiducia".to_string(),
            name: "core".to_string(),
            version: VERSION.to_string(),
            target: Some("node".to_string()),
        },
        publications: vec![
            publication(
                LINUX_PACKAGE,
                NativePublicationKind::Platform,
                Some(linux),
                'b',
            ),
            wrapper,
            publication(
                DARWIN_PACKAGE,
                NativePublicationKind::Platform,
                Some(darwin),
                'c',
            ),
        ],
    }
}

fn cargo_portable_record() -> NativeRegistryAdapterRecord {
    NativeRegistryAdapterRecord {
        schema: NATIVE_REGISTRY_ADAPTER_SCHEMA_V1.to_string(),
        registry: NativeRegistry::Cargo,
        source: ZedNativePackageIdentity {
            org: "fiducia".to_string(),
            name: "core".to_string(),
            version: VERSION.to_string(),
            target: Some("rust".to_string()),
        },
        publications: vec![publication(
            "fiducia_core",
            NativePublicationKind::Portable,
            None,
            'd',
        )],
    }
}

fn record_for_kind(kind: &str) -> NativeRegistryAdapterRecord {
    match kind {
        "npm-multiplatform" => npm_multiplatform_record(),
        "cargo-portable" => cargo_portable_record(),
        other => panic!("unsupported corpus record kind: {other}"),
    }
}

fn meta_mut(record: &mut NativeRegistryAdapterRecord) -> &mut NativePublication {
    record
        .publications
        .iter_mut()
        .find(|item| item.kind == NativePublicationKind::Meta)
        .expect("npm corpus contains one meta publication")
}

fn publication_mut<'a>(
    record: &'a mut NativeRegistryAdapterRecord,
    package_name: &str,
) -> &'a mut NativePublication {
    record
        .publications
        .iter_mut()
        .find(|item| item.package.name == package_name)
        .unwrap_or_else(|| panic!("missing corpus publication: {package_name}"))
}

fn apply_mutation(record: &mut NativeRegistryAdapterRecord, mutation: &str) {
    match mutation {
        "unsupported-schema" => {
            record.schema = "zed.native-registry-adapter/v2".to_string();
        }
        "no-publications" => record.publications.clear(),
        "invalid-source-semver" => record.source.version = "1.4".to_string(),
        "build-metadata" => {
            record.source.version = "1.4.2+linux".to_string();
            for item in &mut record.publications {
                item.package.version = "1.4.2+linux".to_string();
            }
        }
        "version-drift" => {
            record.publications[0].package.version = "1.4.3".to_string();
        }
        "invalid-npm-name" => {
            record.publications[0].package.name = "@Fiducia/core".to_string();
        }
        "invalid-cargo-name" => {
            record.publications[0].package.name = "bad/name".to_string();
        }
        "platform-required" => {
            publication_mut(record, LINUX_PACKAGE).platform = None;
        }
        "unexpected-platform" => {
            meta_mut(record).platform = Some(platform("linux", "x64", Some("gnu")));
        }
        "invalid-platform-token" => {
            publication_mut(record, LINUX_PACKAGE)
                .platform
                .as_mut()
                .expect("platform selector")
                .arch = "ARM64".to_string();
        }
        "duplicate-meta-platform" => {
            let duplicate = meta_mut(record).platform_packages[0].clone();
            meta_mut(record).platform_packages.push(duplicate);
        }
        "platform-package-mismatch" => {
            meta_mut(record).platform_packages[0].package = DARWIN_PACKAGE.to_string();
        }
        "missing-platform-publication" => {
            record
                .publications
                .retain(|item| item.package.name != LINUX_PACKAGE);
        }
        "duplicate-platform-publication" => {
            let duplicate_platform = publication_mut(record, LINUX_PACKAGE).platform.clone();
            record.publications.push(publication(
                "@fiducia/core-linux-arm64-alias",
                NativePublicationKind::Platform,
                duplicate_platform,
                'e',
            ));
        }
        "multiple-meta-packages" => {
            record.publications.push(publication(
                "@fiducia/core-wrapper",
                NativePublicationKind::Meta,
                None,
                'e',
            ));
        }
        "platform-packages-not-allowed" => {
            let selection = meta_mut(record).platform_packages[0].clone();
            publication_mut(record, LINUX_PACKAGE)
                .platform_packages
                .push(selection);
        }
        "invalid-sha256" => {
            record.publications[0].artifact.sha256 = "A".repeat(64);
        }
        "empty-artifact" => {
            record.publications[0].artifact.size = 0;
        }
        "duplicate-package-version" => {
            record.publications.push(record.publications[0].clone());
        }
        other => panic!("unsupported corpus mutation: {other}"),
    }
}

fn error_name(error: &NativeRegistryError) -> &'static str {
    match error {
        NativeRegistryError::UnsupportedSchema { .. } => "UnsupportedSchema",
        NativeRegistryError::NoPublications => "NoPublications",
        NativeRegistryError::InvalidPackageName { .. } => "InvalidPackageName",
        NativeRegistryError::InvalidSemver { .. } => "InvalidSemver",
        NativeRegistryError::BuildMetadataNotAllowed { .. } => "BuildMetadataNotAllowed",
        NativeRegistryError::VersionDrift { .. } => "VersionDrift",
        NativeRegistryError::InvalidIdentityComponent { .. } => "InvalidIdentityComponent",
        NativeRegistryError::InvalidPlatformToken { .. } => "InvalidPlatformToken",
        NativeRegistryError::InvalidSha256 { .. } => "InvalidSha256",
        NativeRegistryError::EmptyArtifact { .. } => "EmptyArtifact",
        NativeRegistryError::PlatformRequired { .. } => "PlatformRequired",
        NativeRegistryError::UnexpectedPlatform { .. } => "UnexpectedPlatform",
        NativeRegistryError::PlatformPackagesNotAllowed { .. } => "PlatformPackagesNotAllowed",
        NativeRegistryError::DuplicateMetaPlatform { .. } => "DuplicateMetaPlatform",
        NativeRegistryError::DuplicatePackageVersion { .. } => "DuplicatePackageVersion",
        NativeRegistryError::MultipleMetaPackages => "MultipleMetaPackages",
        NativeRegistryError::DuplicatePlatformPublication { .. } => "DuplicatePlatformPublication",
        NativeRegistryError::PlatformPackageMismatch { .. } => "PlatformPackageMismatch",
        NativeRegistryError::MissingPlatformPublication { .. } => "MissingPlatformPublication",
        NativeRegistryError::Serialization(_) => "Serialization",
    }
}

fn assert_unique_ids<'a>(category: &str, ids: impl IntoIterator<Item = &'a str>) {
    let mut seen = BTreeSet::new();
    for id in ids {
        assert!(seen.insert(id), "duplicate {category} case id: {id}");
    }
}

fn validate_corpus_shape(corpus: &Corpus) {
    assert_eq!(corpus.schema, "zed.version-interop-corpus/v1");
    assert_eq!(corpus.interface_commit, INTERFACE_COMMIT);
    assert_eq!(corpus.precedence.len(), 3, "precedence corpus shrank");
    assert_eq!(corpus.collisions.len(), 4, "collision corpus shrank");
    assert_eq!(
        corpus.valid_records.len(),
        2,
        "positive record corpus shrank"
    );
    assert_eq!(
        corpus.invalid_records.len(),
        19,
        "negative record corpus shrank"
    );

    assert_unique_ids(
        "precedence",
        corpus.precedence.iter().map(|case| case.id.as_str()),
    );
    assert_unique_ids(
        "collision",
        corpus.collisions.iter().map(|case| case.id.as_str()),
    );
    assert_unique_ids(
        "valid record",
        corpus.valid_records.iter().map(|case| case.id.as_str()),
    );
    assert_unique_ids(
        "invalid record",
        corpus.invalid_records.iter().map(|case| case.id.as_str()),
    );
}

fn run_precedence_cases(corpus: &Corpus) {
    for case in &corpus.precedence {
        let actual = semver_precedence_identity(&case.input)
            .unwrap_or_else(|error| panic!("precedence case {} failed: {error}", case.id));
        assert_eq!(actual, case.expected, "precedence case {}", case.id);
    }
}

fn run_collision_cases(corpus: &Corpus) {
    for case in &corpus.collisions {
        let actual = native_versions_collide(&case.left, &case.right)
            .unwrap_or_else(|error| panic!("collision case {} failed: {error}", case.id));
        assert_eq!(actual, case.expected, "collision case {}", case.id);
    }
}

fn run_valid_records(corpus: &Corpus) {
    for case in &corpus.valid_records {
        let record = record_for_kind(&case.kind);
        record
            .validate()
            .unwrap_or_else(|error| panic!("valid record {} failed: {error}", case.id));
        let canonical = record
            .canonical_json_bytes()
            .unwrap_or_else(|error| panic!("valid record {} was not canonical: {error}", case.id));

        let round_trip: NativeRegistryAdapterRecord = serde_json::from_slice(&canonical)
            .unwrap_or_else(|error| panic!("valid record {} did not round trip: {error}", case.id));
        round_trip
            .validate()
            .unwrap_or_else(|error| panic!("round-tripped record {} failed: {error}", case.id));

        let mut reordered = record.clone();
        reordered.publications.reverse();
        for publication in &mut reordered.publications {
            publication.platform_packages.reverse();
        }
        assert_eq!(
            canonical,
            reordered.canonical_json_bytes().unwrap(),
            "canonical ordering drifted for {}",
            case.id
        );
    }
}

fn run_invalid_records(corpus: &Corpus) {
    for case in &corpus.invalid_records {
        let mut record = record_for_kind(&case.base);
        apply_mutation(&mut record, &case.mutation);
        let error = match record.validate() {
            Ok(()) => panic!("invalid record {} unexpectedly validated", case.id),
            Err(error) => error,
        };
        assert_eq!(
            error_name(&error),
            case.expected_error,
            "invalid record {} returned {error}",
            case.id
        );
    }
}

fn main() {
    let corpus: Corpus =
        serde_json::from_str(include_str!("../cases.json")).expect("corpus JSON must parse");
    validate_corpus_shape(&corpus);
    run_precedence_cases(&corpus);
    run_collision_cases(&corpus);
    run_valid_records(&corpus);
    run_invalid_records(&corpus);

    println!(
        "certified {} precedence, {} collision, {} valid, and {} invalid cases against {}",
        corpus.precedence.len(),
        corpus.collisions.len(),
        corpus.valid_records.len(),
        corpus.invalid_records.len(),
        corpus.interface_commit
    );
}
