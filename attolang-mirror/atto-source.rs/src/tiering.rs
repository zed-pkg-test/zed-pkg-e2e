//! Pure, allocation-free runtime-policy primitives for future AOT/JIT backends.
//!
//! This module does not emit JIT code or relocate objects. It provides bounded
//! trace counters and compile-time layout recommendations. Executable-memory
//! permission and ownership proofs must be enforced independently by a runtime.
use crate::profile::BuildProfile;
use crate::ref_layout::LayoutKind;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DeploymentTarget {
    NativeServer,
    NativeDesktop,
    BrowserWasm,
    Wasi,
    Embedded,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum OptimizationMode {
    Aot,
    ProfileGuidedAot,
    AdaptiveJit,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TrustLevel {
    Trusted,
    Untrusted,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PlanError {
    JitForbiddenByProfile,
    JitForbiddenByTarget,
    JitRequiresTrustedHost,
    InvalidThreshold,
    TraceSlotOutOfRange,
    NoLayoutEvidence,
    InvalidVectorLanes,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ExecutionPlan {
    target: DeploymentTarget,
    optimization: OptimizationMode,
}

impl ExecutionPlan {
    /// No consumer can disable the AOT baseline through mutable/public fields.
    pub const fn requires_aot_baseline(self) -> bool {
        true
    }

    pub const fn target(self) -> DeploymentTarget {
        self.target
    }

    pub const fn optimization(self) -> OptimizationMode {
        self.optimization
    }

    /// Validate an optimization request without asserting target compilation,
    /// actual executable-memory provisioning or JIT implementation support.
    pub fn admit(
        target: DeploymentTarget,
        profile: BuildProfile,
        optimization: OptimizationMode,
        trust: TrustLevel,
        executable_memory_allowed: bool,
    ) -> Result<Self, PlanError> {
        if optimization == OptimizationMode::AdaptiveJit {
            if matches!(profile, BuildProfile::StrictAot | BuildProfile::HardRt) {
                return Err(PlanError::JitForbiddenByProfile);
            }
            if !matches!(
                target,
                DeploymentTarget::NativeServer | DeploymentTarget::NativeDesktop
            ) {
                return Err(PlanError::JitForbiddenByTarget);
            }
            if trust != TrustLevel::Trusted || !executable_memory_allowed {
                return Err(PlanError::JitRequiresTrustedHost);
            }
        }
        Ok(Self {
            target,
            optimization,
        })
    }
}

/// A fixed-capacity, zero-allocation counter bank. The only hot path is a
/// bounds-checked saturating integer increment. No code is generated here.
#[derive(Debug, Eq, PartialEq)]
pub struct TraceCounters<const N: usize> {
    counts: [u32; N],
    hot_threshold: u32,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TraceEvent {
    Cold,
    BecameHot,
    AlreadyHot,
}

impl<const N: usize> TraceCounters<N> {
    pub fn new(hot_threshold: u32) -> Result<Self, PlanError> {
        if hot_threshold == 0 {
            return Err(PlanError::InvalidThreshold);
        }
        Ok(Self {
            counts: [0; N],
            hot_threshold,
        })
    }

    pub fn observe(&mut self, slot: usize) -> Result<TraceEvent, PlanError> {
        let count = self
            .counts
            .get_mut(slot)
            .ok_or(PlanError::TraceSlotOutOfRange)?;
        let was_hot = *count >= self.hot_threshold;
        *count = count.saturating_add(1);
        Ok(if was_hot {
            TraceEvent::AlreadyHot
        } else if *count >= self.hot_threshold {
            TraceEvent::BecameHot
        } else {
            TraceEvent::Cold
        })
    }

    pub fn count(&self, slot: usize) -> Option<u32> {
        self.counts.get(slot).copied()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TypeShape {
    OrdinaryClass,
    InvertedClass,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct LayoutEvidence {
    pub row_oriented_reads: u64,
    pub column_oriented_reads: u64,
}

/// Choose a physical layout for the *next compiled representation*. This is
/// not permission to move live rows, invalidate pointers or change an actor
/// heap without borrow, generation, provenance and domain checks.
///
/// Ordinary classes default to AoS unless there is at least a 2:1 column
/// access advantage. Inverted classes have column semantics by declaration.
/// A vector lane hint is used only when a column layout is selected.
pub fn recommend_layout(
    shape: TypeShape,
    evidence: LayoutEvidence,
    vector_lanes: Option<usize>,
) -> Result<LayoutKind, PlanError> {
    if let Some(lanes) = vector_lanes {
        if !(2..=64).contains(&lanes) || !lanes.is_power_of_two() {
            return Err(PlanError::InvalidVectorLanes);
        }
    }
    let prefer_columns = if shape == TypeShape::InvertedClass {
        true
    } else {
        if evidence.row_oriented_reads == 0 && evidence.column_oriented_reads == 0 {
            return Err(PlanError::NoLayoutEvidence);
        }
        evidence.column_oriented_reads > 0
            && (evidence.row_oriented_reads == 0
                || evidence.column_oriented_reads / 2 >= evidence.row_oriented_reads)
    };

    if !prefer_columns {
        return Ok(LayoutKind::Aos);
    }
    Ok(match vector_lanes {
        Some(lanes) => LayoutKind::Aosoa { lanes },
        None => LayoutKind::Soa,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hot_counters_saturate_and_never_retrigger() {
        let mut counters = TraceCounters::<1>::new(u32::MAX).unwrap();
        counters.counts[0] = u32::MAX - 1;
        assert_eq!(counters.observe(0), Ok(TraceEvent::BecameHot));
        assert_eq!(counters.observe(0), Ok(TraceEvent::AlreadyHot));
        assert_eq!(counters.count(0), Some(u32::MAX));
    }
}
