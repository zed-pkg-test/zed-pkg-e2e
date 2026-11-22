//! Bounded, allocation-free-on-record hotness counters for AOT-first tracing.
//!
//! This is a profiling substrate, NOT a JIT compiler, ownership proof or guest
//! runtime integration. It records only numeric site IDs, never guest data or
//! pointer values. Storage is allocated once when the recorder is constructed.

use std::num::NonZeroU64;
use std::sync::atomic::{AtomicU64, Ordering};

const MAX_TRACE_SITES: usize = 4096;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TraceMode {
    /// No counters and no tracing. Appropriate for strict AOT or hard-RT paths.
    AotOnly,
    /// Bounded profiling counters, without executable-code generation.
    CountOnly,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TraceError {
    InvalidConfiguration,
    SiteOutOfRange,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TraceEvent {
    Disabled,
    Counted,
    /// Exactly one caller observes the first threshold crossing per site.
    BecameHot,
}

/// Per-module site counts. No dynamically allocated traces, guest references,
/// locks, syscalls or memory reclamation occur on the event recording path.
#[derive(Debug)]
pub struct TraceSites {
    counters: Box<[AtomicU64]>,
    threshold: NonZeroU64,
    mode: TraceMode,
}

impl TraceSites {
    pub fn new(
        mode: TraceMode,
        site_count: usize,
        threshold: NonZeroU64,
    ) -> Result<Self, TraceError> {
        if site_count > MAX_TRACE_SITES
            || (mode == TraceMode::AotOnly && site_count != 0)
            || (mode == TraceMode::CountOnly && site_count == 0)
        {
            return Err(TraceError::InvalidConfiguration);
        }
        let counters = (0..site_count)
            .map(|_| AtomicU64::new(0))
            .collect::<Vec<_>>()
            .into_boxed_slice();
        Ok(Self {
            counters,
            threshold,
            mode,
        })
    }

    pub fn record(&self, site: usize) -> Result<TraceEvent, TraceError> {
        if self.mode == TraceMode::AotOnly {
            return Ok(TraceEvent::Disabled);
        }
        let counter = self
            .counters
            .get(site)
            .ok_or(TraceError::SiteOutOfRange)?;
        // Atomic CAS saturation prevents counter wraparound, even when many
        // actors report the same site. Relaxed is sufficient: these counts
        // do not publish object data or synchronize guest memory.
        let before = counter
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |n| {
                Some(n.saturating_add(1))
            })
            .expect("update closure always returns Some");
        if before < self.threshold.get() && before + 1 == self.threshold.get() {
            Ok(TraceEvent::BecameHot)
        } else {
            Ok(TraceEvent::Counted)
        }
    }

    pub fn count(&self, site: usize) -> Result<u64, TraceError> {
        self.counters
            .get(site)
            .map(|counter| counter.load(Ordering::Relaxed))
            .ok_or(TraceError::SiteOutOfRange)
    }

    pub fn is_hot(&self, site: usize) -> Result<bool, TraceError> {
        self.count(site).map(|count| count >= self.threshold.get())
    }

    /// Copying a snapshot allocates host metadata. Never call on a hard-RT or
    /// constrained recording path; the event path itself does not allocate.
    pub fn snapshot(&self) -> Vec<u64> {
        self.counters
            .iter()
            .map(|counter| counter.load(Ordering::Relaxed))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn saturation_does_not_wrap_or_reemit_hot_event() {
        let sites = TraceSites::new(
            TraceMode::CountOnly,
            1,
            NonZeroU64::new(2).unwrap(),
        )
        .unwrap();
        sites.counters[0].store(u64::MAX, Ordering::Relaxed);
        assert_eq!(sites.record(0), Ok(TraceEvent::Counted));
        assert_eq!(sites.count(0), Ok(u64::MAX));
    }
}
