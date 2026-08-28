use zed_interfaces::{
    ArtifactFormat, NATIVE_REGISTRY_ADAPTER_SCHEMA_V1, NativeArtifact, NativePackageIdentity,
    NativePlatform, NativePlatformPackage, NativePublication, NativePublicationKind,
    NativeRegistry, NativeRegistryAdapterRecord, NativeRegistryError, ZedNativePackageIdentity,
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

fn publication(name: &str, kind: NativePublicationKind, digit: char) -> NativePublication {
    NativePublication {
        package: NativePackageIdentity {
            name: name.to_string(),
            version: VERSION.to_string(),
        },
        kind,
        platform: None,
        platform_packages: Vec::new(),
        artifact: artifact(digit),
    }
}

fn record() -> NativeRegistryAdapterRecord {
    let linux = platform("linux", "arm64", Some("musl"));
    let darwin = platform("darwin", "arm64", None);

    let mut wrapper = publication(WRAPPER, NativePublicationKind::Meta, 'a');
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

    let mut linux_package = publication(LINUX_PACKAGE, NativePublicationKind::Platform, 'b');
    linux_package.platform = Some(linux);
    let mut darwin_package = publication(DARWIN_PACKAGE, NativePublicationKind::Platform, 'c');
    darwin_package.platform = Some(darwin);

    NativeRegistryAdapterRecord {
        schema: NATIVE_REGISTRY_ADAPTER_SCHEMA_V1.to_string(),
        registry: NativeRegistry::Npm,
        source: ZedNativePackageIdentity {
            org: "fiducia".to_string(),
            name: "core".to_string(),
            version: VERSION.to_string(),
            target: Some("node".to_string()),
        },
        publications: vec![wrapper, linux_package, darwin_package],
    }
}

fn meta(record: &mut NativeRegistryAdapterRecord) -> &mut NativePublication {
    record
        .publications
        .iter_mut()
        .find(|publication| publication.kind == NativePublicationKind::Meta)
        .expect("fixture contains one meta package")
}

#[test]
fn rejects_more_than_one_portable_publication() {
    let mut candidate = record();
    candidate.publications.extend([
        publication(
            "@fiducia/core-portable",
            NativePublicationKind::Portable,
            'd',
        ),
        publication(
            "@fiducia/core-portable-extra",
            NativePublicationKind::Portable,
            'e',
        ),
    ]);

    assert!(matches!(
        candidate.validate(),
        Err(NativeRegistryError::MultiplePortablePackages)
    ));
}

#[test]
fn rejects_empty_meta_platform_selection() {
    let mut candidate = record();
    meta(&mut candidate).platform_packages.clear();

    assert!(matches!(
        candidate.validate(),
        Err(NativeRegistryError::MetaPackageRequiresPlatformSelections { .. })
    ));
}

#[test]
fn rejects_unselected_platform_publication() {
    let mut candidate = record();
    meta(&mut candidate).platform_packages.pop();

    assert!(matches!(
        candidate.validate(),
        Err(NativeRegistryError::UnselectedPlatformPublication { .. })
    ));
}

#[test]
fn accepts_platform_only_publication_family() {
    let mut candidate = record();
    candidate
        .publications
        .retain(|publication| publication.kind == NativePublicationKind::Platform);

    candidate
        .validate()
        .expect("platform-only publication family remains valid");
}
