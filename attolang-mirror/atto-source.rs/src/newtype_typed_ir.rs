//! Canonical nominal/newtype + typed Result/Error identity for guest AOT.
//!
//! This is a semantic type graph, NOT a Wasm representation coercion. A
//! private compiled guest currently lowers a *proven* Result<Newtype,RangeError>
//! to the existing ADT encoding only after source checking. Public ABI,
//! dynamic Error objects and other generic payloads remain fail-closed.
//!
//! Types are structural ONLY in generic constructor arguments after each
//! nominal identity has been resolved. Error inheritance does not imply
//! Result covariance; generic Result slots are invariant.
use std::collections::BTreeMap;

use crate::diag::{Diagnostic, Pos};
use crate::newtype_const_generic_admission::Interval;

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum ErrorClass {
    Error,
    RangeError,
}

impl ErrorClass {
    pub fn parent(&self) -> Option<Self> {
        match self {
            Self::Error => None,
            Self::RangeError => Some(Self::Error),
        }
    }

    pub fn is_subclass_of(&self, parent: &Self) -> bool {
        let mut at = Some(self.clone());
        while let Some(class) = at {
            if &class == parent {
                return true;
            }
            at = class.parent();
        }
        false
    }

    /// Stable semantic category code, NOT a serialized object pointer.
    pub fn category_code(&self) -> u32 {
        match self {
            Self::Error => 0x4552_5230,
            Self::RangeError => 0x5241_4e47,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Ty {
    I64,
    /// A declaration has a private module-scoped identity: the name alone
    /// is never sufficient to authorize cross-module equivalence.
    Nominal(String),
    Error(ErrorClass),
    Result {
        ok: Box<Ty>,
        err: Box<Ty>,
    },
}

impl Ty {
    pub fn checked(nominal: String) -> Self {
        Self::Result {
            ok: Box::new(Self::Nominal(nominal)),
            err: Box::new(Self::Error(ErrorClass::RangeError)),
        }
    }

    pub fn checked_nominal(&self) -> Option<&str> {
        match self {
            Self::Result { ok, err }
                if **err == Self::Error(ErrorClass::RangeError) =>
            {
                match ok.as_ref() {
                    Self::Nominal(name) => Some(name),
                    _ => None,
                }
            }
            _ => None,
        }
    }

    /// AOT-only physical preview of the currently executable private subset.
    /// No generic Result<Error, T> layout is assumed for unsupported values.
    pub fn private_wasm_lowered_type(&self) -> Option<&'static str> {
        match self {
            Self::I64 | Self::Nominal(_) => Some("int"),
            Self::Result { .. } if self.checked_nominal().is_some() => {
                Some("Result<int,string>")
            }
            Self::Error(_) | Self::Result { .. } => None,
        }
    }
}

/// The owner module is part of the type identity, unlike the i64 repr.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct NominalIdentity {
    pub module: String,
    pub name: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct NominalDescriptor {
    pub identity: NominalIdentity,
    pub closed_interval: Interval,
    pub representation_bits: u16,
}

#[derive(Clone, Debug, Default)]
pub struct NominalGraph {
    declarations: BTreeMap<NominalIdentity, NominalDescriptor>,
}

impl NominalGraph {
    pub fn declare(
        &mut self,
        module: &str,
        name: &str,
        interval: Interval,
        pos: Pos,
    ) -> Result<NominalIdentity, Diagnostic> {
        if module.is_empty() || name.is_empty() || name.starts_with("__atto_") {
            return Err(Diagnostic::new(pos, "E_NEWTYPE_IDENTITY", "invalid nominal scope"));
        }
        if interval.low > interval.high {
            return Err(Diagnostic::new(pos, "E_RANGE_INVALID", "empty newtype interval"));
        }
        let identity = NominalIdentity {
            module: module.to_owned(),
            name: name.to_owned(),
        };
        if self.declarations.contains_key(&identity) {
            return Err(Diagnostic::new(pos, "E_DUPLICATE", "duplicate nominal declaration"));
        }
        self.declarations.insert(
            identity.clone(),
            NominalDescriptor {
                identity: identity.clone(),
                closed_interval: interval,
                representation_bits: 64,
            },
        );
        Ok(identity)
    }

    pub fn get(&self, id: &NominalIdentity) -> Option<&NominalDescriptor> {
        self.declarations.get(id)
    }

    pub fn validate_const(
        &self,
        id: &NominalIdentity,
        value: i64,
        pos: Pos,
    ) -> Result<(), Diagnostic> {
        let d = self.get(id).ok_or_else(|| {
            Diagnostic::new(pos, "E_NEWTYPE_IDENTITY", "unknown nominal owner")
        })?;
        if !(d.closed_interval.low..=d.closed_interval.high).contains(&value) {
            return Err(Diagnostic::new(
                pos,
                "E_RANGE_INVALID",
                "literal falls outside closed nominal bounds",
            ));
        }
        Ok(())
    }

    pub fn identical(&self, a: &NominalIdentity, b: &NominalIdentity) -> bool {
        a == b && self.get(a).is_some()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn p() -> Pos {
        Pos { line: 1, col: 1 }
    }

    #[test]
    fn checked_result_has_exact_nominal_success_and_error_type() {
        let port = Ty::checked("Port".to_owned());
        let user = Ty::checked("UserId".to_owned());
        assert_ne!(port, user);
        assert_eq!(port.checked_nominal(), Some("Port"));
        assert_eq!(port.private_wasm_lowered_type(), Some("Result<int,string>"));
        assert_eq!(
            Ty::Result {
                ok: Box::new(Ty::Nominal("Port".into())),
                err: Box::new(Ty::Error(ErrorClass::Error)),
            }
            .private_wasm_lowered_type(),
            None,
            "Error supertype must never authorize an unchecked Result layout"
        );
    }

    #[test]
    fn range_error_is_an_error_subclass_but_result_is_invariant() {
        assert!(ErrorClass::RangeError.is_subclass_of(&ErrorClass::Error));
        assert!(!ErrorClass::Error.is_subclass_of(&ErrorClass::RangeError));
        assert_ne!(ErrorClass::Error.category_code(), ErrorClass::RangeError.category_code());
        let strict = Ty::checked("Port".into());
        let supertype = Ty::Result {
            ok: Box::new(Ty::Nominal("Port".into())),
            err: Box::new(Ty::Error(ErrorClass::Error)),
        };
        assert_ne!(strict, supertype);
    }

    #[test]
    fn nominal_owner_is_part_of_identity_even_with_same_i64_layout() {
        let mut g = NominalGraph::default();
        let bounds = Interval { low: 1, high: 65535 };
        let a = g.declare("+pkgA", "Port", bounds, p()).unwrap();
        let b = g.declare("+pkgB", "Port", bounds, p()).unwrap();
        assert!(!g.identical(&a, &b));
        assert_eq!(g.get(&a).unwrap().representation_bits, 64);
        assert_eq!(g.declare("+pkgA", "Port", bounds, p()).unwrap_err().code, "E_DUPLICATE");
        assert!(g.validate_const(&a, 1, p()).is_ok());
        assert!(g.validate_const(&a, 65535, p()).is_ok());
        assert_eq!(g.validate_const(&a, 0, p()).unwrap_err().code, "E_RANGE_INVALID");
        assert_eq!(g.validate_const(&a, 65536, p()).unwrap_err().code, "E_RANGE_INVALID");
    }

    #[test]
    fn unchecked_error_and_non_nominal_result_never_lower_to_guest() {
        let impossible = Ty::Result {
            ok: Box::new(Ty::I64),
            err: Box::new(Ty::Error(ErrorClass::RangeError)),
        };
        assert_eq!(impossible.checked_nominal(), None);
        assert_eq!(impossible.private_wasm_lowered_type(), None);
        assert_eq!(Ty::Error(ErrorClass::RangeError).private_wasm_lowered_type(), None);
    }
}
