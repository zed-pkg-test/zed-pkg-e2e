//! Canonical source-owned module identities for the bounded guest linker.
//!
//! The identifiers survive parsing/import binding even though the initial
//! codegen still lowers linked modules into one guest. They do not grant
//! visibility; every import edge must be checked separately.
use crate::diag::{Diagnostic, Pos};

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct QualifiedModuleId {
    pub package: String,
    pub namespace: String,
    pub source_path: String,
    pub module: String,
}
impl QualifiedModuleId {
    pub fn new(package: &str, namespace: &str, source_path: &str, module: &str) -> Result<Self, Diagnostic> {
        if package.is_empty()
            || source_path.is_empty()
            || namespace.is_empty()
            || module.is_empty()
            || package.len() > 128
            || namespace.len() > 64
            || source_path.len() > 256
            || module.len() > 64
            || !package.is_ascii()
            || !namespace.is_ascii()
            || !source_path.is_ascii()
            || !module.is_ascii()
        {
            return Err(Diagnostic::new(
                Pos::default(),
                "E_SYMBOL_ID",
                "invalid bounded source-owned module identity",
            ));
        }
        Ok(Self {
            package: package.into(),
            namespace: namespace.into(),
            source_path: source_path.into(),
            module: module.into(),
        })
    }
    /// Canonical length-prefixed key, without delimiter ambiguity.
    pub fn canonical_key(&self) -> Vec<u8> {
        let mut key = b"atto.module.id.v1\0".to_vec();
        for s in [&self.package, &self.namespace, &self.source_path, &self.module] {
            key.extend_from_slice(&(s.len() as u32).to_le_bytes());
            key.extend_from_slice(s.as_bytes());
        }
        key
    }
    /// Source-spelling-independent 128-bit SHA-256-based internal name.
    /// A cryptographic truncation collision is checked at link admission.
    /// The prefix is reserved against user-authored source identifiers.
    pub fn internal_name(&self) -> String {
        let hex = crate::sha256::hex_digest(&self.canonical_key());
        format!("__atto_q_{}", &hex[..32])
    }
}
#[cfg(test)]
mod tests {
    use super::QualifiedModuleId;
    #[test]
    fn identity_separates_package_namespace_and_path_not_just_leaf() {
        let a = QualifiedModuleId::new("shop@1.0", "billing", "lib/a.atto", "Math").unwrap();
        let b = QualifiedModuleId::new("shop@1.0", "analytics", "lib/a.atto", "Math").unwrap();
        let c = QualifiedModuleId::new("other@1.0", "billing", "lib/a.atto", "Math").unwrap();
        let d = QualifiedModuleId::new("shop@1.0", "billing", "lib/b.atto", "Math").unwrap();
        for different in [&b, &c, &d] {
            assert_ne!(a.internal_name(), different.internal_name());
        }
        assert_eq!(
            a.internal_name(),
            QualifiedModuleId::new("shop@1.0", "billing", "lib/a.atto", "Math")
                .unwrap()
                .internal_name()
        );
        assert!(a.internal_name().len() <= 64);
    }
    #[test]
    fn unambiguous_length_prefixed_identity() {
        let a = QualifiedModuleId::new("ab", "c", "lib/a.atto", "Math").unwrap();
        let b = QualifiedModuleId::new("a", "bc", "lib/a.atto", "Math").unwrap();
        assert_ne!(a.canonical_key(), b.canonical_key());
        assert_ne!(a.internal_name(), b.internal_name());
    }
}
