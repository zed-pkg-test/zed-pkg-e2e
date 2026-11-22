//! Typed, fail-closed ownership planning for non-Copy tuple elements.
//!
//! Static IR only: this module does not allocate guest tuple values or issue
//! guest references. The runtime compiler must continue rejecting object
//! tuples until scoped-loan lowering, drop and actor transfer are proven.
//! Every tuple has one owner: non-Copy names are rooted loans, not copies.
use std::collections::{BTreeMap, BTreeSet};
use crate::diag::{Diagnostic, Pos};
use crate::tuple_binding_admission::{BindingMode, TupleMember};

type R<T> = Result<T, Diagnostic>;
const MAX_ELEMENTS: usize = 16;
fn err(pos: Pos, code: &'static str, message: &str) -> Diagnostic {
    Diagnostic::new(pos, code, message)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Scalar { Int, Bool, Float, String }
impl Scalar {
    fn matches(self, s: &str) -> bool {
        match self {
            Self::Int => s == "int",
            Self::Bool => s == "bool",
            Self::Float => s == "float" || s == "f64",
            Self::String => s == "string" || s == "String",
        }
    }
}

/// Nominal IDs must come from a resolved declaration table, never inferred
/// solely from an identifier spelling or a same-shape class.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ElementType {
    Copy(Scalar),
    OwnedClass { class_id: u32, name: String },
}
impl ElementType {
    pub fn is_copy(&self) -> bool { matches!(self, Self::Copy(_)) }
    fn matches_annotation(&self, text: &str) -> bool {
        match self {
            Self::Copy(s) => s.matches(text),
            Self::OwnedClass { name, .. } => name == text,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TupleType { pub elements: Vec<ElementType> }
impl TupleType {
    pub fn new(elements: Vec<ElementType>, pos: Pos) -> R<Self> {
        if elements.len() > MAX_ELEMENTS {
            return Err(err(pos, "E_SOURCE_LIMIT", "tuple ownership IR supports at most 16 elements"));
        }
        if elements.iter().any(|elt| matches!(elt, ElementType::OwnedClass { name, .. } if name.is_empty())) {
            return Err(err(pos, "E_UNKNOWN_TYPE", "owned tuple element needs a resolved nominal class"));
        }
        Ok(Self { elements })
    }
    pub fn has_owners(&self) -> bool { self.elements.iter().any(|x| !x.is_copy()) }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LoanKind { Shared, Exclusive }
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LoanTarget { Root, Element(usize) }
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Loan {
    pub id: u64,
    pub epoch: u64,
    pub scope: u32,
    pub kind: LoanKind,
    pub target: LoanTarget,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ProjectionOp { CopyValue, Borrow { loan_id: u64, kind: LoanKind } }
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Projection {
    pub name: String,
    pub index: usize,
    pub binding: BindingMode,
    pub operation: ProjectionOp,
    /// Referent mutability is independent of local name rebinding.
    pub mutable_referent: bool,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum SlotState { Available, Moved }

/// Clone is used only to stage an atomic pattern preflight. It is not a
/// clone of an actual owned guest value or an aliasing capability.
#[derive(Clone, Debug)]
pub struct TupleOwner {
    ty: TupleType,
    mode: BindingMode,
    constexpr: bool,
    states: Vec<SlotState>,
    loans: BTreeMap<u64, Loan>,
    next_loan: u64,
    epoch: u64,
}
impl TupleOwner {
    pub fn new(ty: TupleType, mode: BindingMode, constexpr: bool, pos: Pos) -> R<Self> {
        if mode == BindingMode::Const && (!constexpr || ty.has_owners()) {
            return Err(err(pos, "E_CONST", "const tuple needs constexpr Copy fields, not object storage"));
        }
        Ok(Self {
            states: vec![SlotState::Available; ty.elements.len()],
            ty, mode, constexpr, loans: BTreeMap::new(), next_loan: 1, epoch: 0,
        })
    }
    pub fn shape(&self) -> &TupleType { &self.ty }
    pub fn active_loans(&self) -> usize { self.loans.len() }
    pub fn was_moved(&self, index: usize) -> bool {
        self.states.get(index) == Some(&SlotState::Moved)
    }
    fn checked_field(&self, index: usize, pos: Pos) -> R<&ElementType> {
        let ty = self.ty.elements.get(index).ok_or_else(||
            err(pos, "E_ARITY", "tuple element is out of bounds"))?;
        if self.states[index] == SlotState::Moved {
            return Err(err(pos, "E_MOVE", "tuple element already taken"));
        }
        Ok(ty)
    }
    fn conflicts(&self, target: LoanTarget, kind: LoanKind) -> bool {
        self.loans.values().any(|l| {
            let same_storage = match (target, l.target) {
                (LoanTarget::Root, _) | (_, LoanTarget::Root) => true,
                (LoanTarget::Element(a), LoanTarget::Element(b)) => a == b,
            };
            same_storage && (kind == LoanKind::Exclusive || l.kind == LoanKind::Exclusive)
        })
    }
    fn begin_loan(&mut self, target: LoanTarget, kind: LoanKind, scope: u32, pos: Pos) -> R<u64> {
        if let LoanTarget::Element(i) = target { self.checked_field(i, pos)?; }
        if target == LoanTarget::Root && self.states.contains(&SlotState::Moved) {
            return Err(err(pos, "E_MOVE", "cannot borrow a partially moved tuple root"));
        }
        if self.conflicts(target, kind) {
            return Err(err(pos, "E_BORROW", "overlapping tuple loans require compatible shared access"));
        }
        let next = self.next_loan.checked_add(1).ok_or_else(||
            err(pos, "E_SOURCE_LIMIT", "tuple loan identifier overflow"))?;
        let id = self.next_loan;
        self.next_loan = next;
        self.loans.insert(id, Loan { id, epoch: self.epoch, scope, kind, target });
        Ok(id)
    }
    pub fn borrow_root(&mut self, kind: LoanKind, scope: u32, pos: Pos) -> R<u64> {
        if self.mode == BindingMode::Const && kind == LoanKind::Exclusive {
            return Err(err(pos, "E_CONST_MUT_TARGET", "cannot mutably loan a constexpr tuple root"));
        }
        self.begin_loan(LoanTarget::Root, kind, scope, pos)
    }
    pub fn borrow_field(&mut self, index: usize, kind: LoanKind, scope: u32, pos: Pos) -> R<u64> {
        self.begin_loan(LoanTarget::Element(index), kind, scope, pos)
    }
    pub fn end_loan(&mut self, id: u64, pos: Pos) -> R<()> {
        if self.loans.remove(&id).is_none() {
            return Err(err(pos, "E_BORROW", "tuple loan is unknown or already released"));
        }
        Ok(())
    }
    /// Lexical scope expiry does not allow a reference to escape. The caller
    /// must separately prove all uses of these IDs stay inside the scope.
    pub fn end_scope(&mut self, scope: u32) {
        self.loans.retain(|_, l| l.scope != scope);
    }
    /// Every non-Copy binder creates a rooted loan; plain nonprimitive
    /// function arguments are shared loans by default, not owner transfers.
    pub fn project(&mut self, members: &[TupleMember], scope: u32, pos: Pos)
        -> R<Vec<Projection>>
    {
        if members.len() != self.ty.elements.len() {
            return Err(err(pos, "E_ARITY", "tuple binder count does not match typed shape"));
        }
        let mut proposed = self.clone();
        let mut result = Vec::new();
        let mut names = BTreeSet::new();
        for (i, member) in members.iter().enumerate() {
            let Some(name) = &member.name else { continue; };
            if !names.insert(name.as_str()) {
                return Err(err(pos, "E_DUPLICATE", "tuple element binding name used more than once"));
            }
            let field = proposed.checked_field(i, pos)?;
            if member.annotation.as_ref().is_some_and(|text| !field.matches_annotation(text)) {
                return Err(err(pos, "E_TYPE", "tuple element type annotation mismatch"));
            }
            if member.mode == BindingMode::Const && member.mutable_referent {
                return Err(err(pos, "E_CONST_MUT_TARGET", "const mut element is invalid"));
            }
            let operation = match field {
                ElementType::Copy(_) => {
                    if member.mode == BindingMode::Const && !proposed.constexpr {
                        return Err(err(pos, "E_CONST", "const element needs proven static provenance"));
                    }
                    ProjectionOp::CopyValue
                }
                ElementType::OwnedClass { .. } => {
                    if member.mode == BindingMode::Const {
                        return Err(err(pos, "E_CONST", "object tuple elements are not constexpr"));
                    }
                    let kind = if member.mutable_referent {
                        LoanKind::Exclusive
                    } else { LoanKind::Shared };
                    let loan_id = proposed.borrow_field(i, kind, scope, pos)?;
                    ProjectionOp::Borrow { loan_id, kind }
                }
            };
            result.push(Projection {
                name: name.clone(), index: i, binding: member.mode, operation,
                mutable_referent: member.mutable_referent,
            });
        }
        *self = proposed;
        Ok(result)
    }
    pub fn take_field(&mut self, index: usize, pos: Pos) -> R<ElementType> {
        let ty = self.checked_field(index, pos)?.clone();
        if self.loans.values().any(|loan|
            loan.target == LoanTarget::Root || loan.target == LoanTarget::Element(index)) {
            return Err(err(pos, "E_BORROW", "take! cannot move a loaned tuple element"));
        }
        self.states[index] = SlotState::Moved;
        Ok(ty)
    }
    pub fn read_copy(&self, index: usize, pos: Pos) -> R<()> {
        if !self.checked_field(index, pos)?.is_copy() {
            return Err(err(pos, "E_OWNERSHIP", "non-Copy tuple access requires a rooted loan or take!"));
        }
        if self.loans.values().any(|loan| loan.kind == LoanKind::Exclusive
            && (loan.target == LoanTarget::Root || loan.target == LoanTarget::Element(index))) {
            return Err(err(pos, "E_BORROW", "exclusive loan prevents reading the original tuple field"));
        }
        Ok(())
    }
    /// Root let/val/const changes *name binding*, never a referent's mutability.
    /// A partial move requires a destructor/drop proof that is not yet emitted.
    pub fn rebind_root(&mut self, next_type: &TupleType, pos: Pos) -> R<()> {
        if self.mode != BindingMode::Let {
            return Err(err(pos, "E_IMMUTABLE", "val/const tuple owner cannot be rebound"));
        }
        if self.ty != *next_type {
            return Err(err(pos, "E_TYPE", "rebound tuple must have identical resolved element types"));
        }
        if !self.loans.is_empty() {
            return Err(err(pos, "E_BORROW", "tuple root cannot rebind while any field loan is live"));
        }
        if self.states.contains(&SlotState::Moved) {
            return Err(err(pos, "E_MOVE", "partial move needs a checked cleanup plan before root rebind"));
        }
        self.epoch = self.epoch.checked_add(1).ok_or_else(||
            err(pos, "E_SOURCE_LIMIT", "tuple root epoch overflow"))?;
        // Rebinding could install a runtime value: previous literal
        // provenance must not certify a subsequent const projection.
        self.constexpr = false;
        Ok(())
    }
    pub fn copy_field(&self, index: usize, pos: Pos) -> R<()> {
        if self.checked_field(index, pos)?.is_copy() {
            self.read_copy(index, pos)
        } else {
            Err(err(pos, "E_UNSUPPORTED", "copy! or copy!! of an owned tuple field needs a proved clone plan"))
        }
    }
    pub fn escape_loan(&self, id: u64, pos: Pos) -> R<()> {
        if !self.loans.contains_key(&id) {
            return Err(err(pos, "E_BORROW", "tuple element loan is not live"));
        }
        Err(err(pos, "E_BORROW", "tuple field loan cannot escape its owning lexical scope"))
    }
    pub fn send_to_actor(&self, index: usize, pos: Pos) -> R<()> {
        self.checked_field(index, pos)?;
        Err(err(pos, "E_UNSUPPORTED",
            "tuple actor transfer needs Sendable proof and a guest transfer ABI"))
    }
}
