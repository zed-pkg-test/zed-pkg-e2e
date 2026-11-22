//! Fixed-capacity AoS/SoA access-shape observations for future AOT profiling.
//!
//! The caller must own this trace exclusively. No guest data, addresses,
//! allocations, JIT code or actor capabilities are captured or issued.

pub const MAX_LAYOUT_TRACE_FIELDS: usize = 64;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LayoutHint {
    Insufficient,
    Aos,
    Soa,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LayoutTraceError {
    InvalidFieldCount,
}

/// An owner-local trace. Mutable self is required for every state change,
/// avoiding a hidden lock or non-atomic cross-actor mutation.
#[derive(Debug)]
pub struct LayoutAccessTrace {
    rows: u64,
    fields: usize,
    enabled: bool,
    sealed: bool,
    hits: [u64; MAX_LAYOUT_TRACE_FIELDS],
    samples: u64,
    row_local: u64,
    column_scan: u64,
    previous: Option<(u64, usize)>,
}

impl LayoutAccessTrace {
    pub fn new(
        rows: u64,
        fields: usize,
        enabled: bool,
        sealed: bool,
    ) -> Result<Self, LayoutTraceError> {
        if fields == 0 || fields > MAX_LAYOUT_TRACE_FIELDS {
            return Err(LayoutTraceError::InvalidFieldCount);
        }
        Ok(Self {
            rows,
            fields,
            enabled,
            sealed,
            hits: [0; MAX_LAYOUT_TRACE_FIELDS],
            samples: 0,
            row_local: 0,
            column_scan: 0,
            previous: None,
        })
    }

    /// Change tracing policy only on an unsealed, exclusively held instance.
    /// Existing counts remain available; only transition history is cleared.
    pub fn configure(&mut self, enabled: bool) -> bool {
        if self.sealed {
            return false;
        }
        self.enabled = enabled;
        self.previous = None;
        true
    }

    /// No allocations, callbacks, syscalls or pointer arithmetic.
    /// Invalid coordinates do not change history or counters.
    pub fn observe(&mut self, row: u64, field: usize) -> bool {
        if !self.enabled || row >= self.rows || field >= self.fields {
            return false;
        }
        self.samples = self.samples.saturating_add(1);
        self.hits[field] = self.hits[field].saturating_add(1);
        if let Some((previous_row, previous_field)) = self.previous {
            if row == previous_row && field != previous_field {
                self.row_local = self.row_local.saturating_add(1);
            } else if field == previous_field && previous_row.checked_add(1) == Some(row) {
                self.column_scan = self.column_scan.saturating_add(1);
            }
        }
        self.previous = Some((row, field));
        true
    }

    pub fn hint(&self) -> LayoutHint {
        if self.column_scan >= 2 && self.column_scan > self.row_local {
            LayoutHint::Soa
        } else if self.row_local >= 2 && self.row_local > self.column_scan {
            LayoutHint::Aos
        } else {
            LayoutHint::Insufficient
        }
    }

    pub fn samples(&self) -> u64 {
        self.samples
    }

    pub fn field_hits(&self, field: usize) -> Option<u64> {
        if field >= self.fields {
            None
        } else {
            Some(self.hits[field])
        }
    }

    pub fn row_local_transitions(&self) -> u64 {
        self.row_local
    }

    pub fn column_scan_transitions(&self) -> u64 {
        self.column_scan
    }

    pub fn enabled(&self) -> bool {
        self.enabled
    }

    pub fn reset(&mut self) {
        self.hits.fill(0);
        self.samples = 0;
        self.row_local = 0;
        self.column_scan = 0;
        self.previous = None;
    }
}
