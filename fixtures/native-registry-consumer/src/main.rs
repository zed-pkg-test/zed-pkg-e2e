use zed_interfaces::{
    ArtifactFormat, NATIVE_REGISTRY_ADAPTER_SCHEMA_V1, NativeArtifact,
    NativePackageIdentity, NativePlatform, NativePlatformPackage, NativePublication,
    NativePublicationKind, NativeRegistry, NativeRegistryAdapterRecord,
    ZedNativePackageIdentity, native_versions_collide, semver_precedence_identity,
};

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
            version: "1.4.2".to_string(),
        },
        kind,
        platform: None,
        platform_packages: Vec::new(),
        artifact: artifact(digest_digit),
    }
}

fn main() {
    let linux_arm64_musl = NativePlatform {
        os: "linux".to_string(),
        arch: "arm64".to_string(),
        libc: Some("musl".to_string()),
    };
    let darwin_arm64 = NativePlatform {
        os: "darwin".to_string(),
        arch: "arm64".to_string(),
        libc: None,
    };

    let mut wrapper = package("@fiducia/core", NativePublicationKind::Meta, 'a');
    wrapper.platform_packages = vec![
        NativePlatformPackage {
            platform: linux_arm64_musl.clone(),
            package: "@fiducia/core-linux-arm64-musl".to_string(),
        },
        NativePlatformPackage {
            platform: darwin_arm64.clone(),
            package: "@fiducia/core-darwin-arm64".to_string(),
        },
    ];

    let mut linux = package(
        "@fiducia/core-linux-arm64-musl",
        NativePublicationKind::Platform,
        'b',
    );
    linux.platform = Some(linux_arm64_musl);

    let mut darwin = package(
        "@fiducia/core-darwin-arm64",
        NativePublicationKind::Platform,
        'c',
    );
    darwin.platform = Some(darwin_arm64);

    let record = NativeRegistryAdapterRecord {
        schema: NATIVE_REGISTRY_ADAPTER_SCHEMA_V1.to_string(),
        registry: NativeRegistry::Npm,
        source: ZedNativePackageIdentity {
            org: "fiducia".to_string(),
            name: "core".to_string(),
            version: "1.4.2".to_string(),
            target: Some("node".to_string()),
        },
        publications: vec![darwin, wrapper, linux],
    };

    record.validate().expect("valid npm publication family");
    let canonical = record
        .canonical_json_bytes()
        .expect("canonical native-registry adapter JSON");
    assert!(!canonical.is_empty());

    assert_eq!(
        semver_precedence_identity("1.4.2+linux-arm64").unwrap(),
        "1.4.2"
    );
    assert!(native_versions_collide("1.4.2+linux", "1.4.2+darwin").unwrap());
    assert!(!native_versions_collide("1.4.2-rc.1", "1.4.2").unwrap());
}
