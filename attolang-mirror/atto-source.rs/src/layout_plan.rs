//! Checked physical layout planning for future inverted-class and ECS lowering.
//!
//! A plan is compiler metadata, not a heap, reference or ownership proof. It
//! describes one authoritative AoS, SoA or AoSoA store and never copies data
//! into a hidden second representation. No guest class/object syntax is
//! admitted by this module; the scalar frontend remains fail-closed.

use crate::ref_layout::{LayoutKind, RefError};

const MAX_FIELDS: usize = 64;
const MAX_LANES: usize = 1024;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct FieldSpec {
    /// Physical byte width of a field's stored representation.
    pub bytes: usize,
    /// Required physical alignment (power of two).
    pub align: usize,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct FieldPlacement {
    base: usize,
    element_stride: usize,
}

/// An immutable, checked address-arithmetic plan. All offsets are relative
/// to a caller-provided allocation; they must not be treated as pointers.
#[derive(Clone, Debug, PartialEq)]
pub struct LayoutPlan {
    kind: LayoutKind,
    rows: usize,
    total_bytes: usize,
    row_stride: usize,
    block_stride: usize,
    fields: Vec<FieldPlacement>,
    widths: Vec<usize>,
}

fn align_up(value: usize, align: usize) -> Result<usize, RefError> {
    value
        .checked_add(align - 1)
        .map(|n| n & !(align - 1))
        .ok_or(RefError::Overflow)
}

impl LayoutPlan {
    /// Plan a single owned store, without allocating any guest storage.
    ///
    /// Reject impossible sizes and non-power-of-two alignments rather than
    /// relying on wrapping pointer arithmetic. AoSoA reserves full trailing
    /// blocks to preserve stable block strides.
    pub fn new(kind: LayoutKind, fields: &[FieldSpec], rows: usize) -> Result<Self, RefError> {
        if fields.is_empty() || fields.len() > MAX_FIELDS {
            return Err(RefError::InvalidLayout);
        }
        let mut max_align = 1;
        for field in fields {
            if field.bytes == 0 || field.align == 0 || !field.align.is_power_of_two() {
                return Err(RefError::InvalidLayout);
            }
            max_align = max_align.max(field.align);
        }
        if let LayoutKind::Aosoa { lanes } = kind {
            if lanes == 0 || lanes > MAX_LANES || !lanes.is_power_of_two() {
                return Err(RefError::InvalidLayout);
            }
        }

        let widths = fields.iter().map(|field| field.bytes).collect();
        let mut plan = Self {
            kind,
            rows,
            total_bytes: 0,
            row_stride: 0,
            block_stride: 0,
            fields: Vec::with_capacity(fields.len()),
            widths,
        };
        // Zero-row stores have zero footprint, irrespective of column padding.
        if rows == 0 {
            plan.fields.resize(
                fields.len(),
                FieldPlacement {
                    base: 0,
                    element_stride: 0,
                },
            );
            return Ok(plan);
        }

        match kind {
            LayoutKind::Aos => {
                let mut cursor = 0usize;
                for field in fields {
                    let base = align_up(cursor, field.align)?;
                    cursor = base.checked_add(field.bytes).ok_or(RefError::Overflow)?;
                    plan.fields.push(FieldPlacement {
                        base,
                        element_stride: 0,
                    });
                }
                plan.row_stride = align_up(cursor, max_align)?;
                plan.total_bytes = plan
                    .row_stride
                    .checked_mul(rows)
                    .ok_or(RefError::Overflow)?;
            }
            LayoutKind::Soa => {
                let mut cursor = 0usize;
                for field in fields {
                    let stride = align_up(field.bytes, field.align)?;
                    let base = align_up(cursor, field.align)?;
                    cursor = stride
                        .checked_mul(rows)
                        .and_then(|bytes| base.checked_add(bytes))
                        .ok_or(RefError::Overflow)?;
                    plan.fields.push(FieldPlacement {
                        base,
                        element_stride: stride,
                    });
                }
                plan.total_bytes = align_up(cursor, max_align)?;
            }
            LayoutKind::Aosoa { lanes } => {
                let mut cursor = 0usize;
                for field in fields {
                    let stride = align_up(field.bytes, field.align)?;
                    let base = align_up(cursor, field.align)?;
                    cursor = stride
                        .checked_mul(lanes)
                        .and_then(|bytes| base.checked_add(bytes))
                        .ok_or(RefError::Overflow)?;
                    plan.fields.push(FieldPlacement {
                        base,
                        element_stride: stride,
                    });
                }
                plan.block_stride = align_up(cursor, max_align)?;
                let blocks = rows / lanes + usize::from(rows % lanes != 0);
                plan.total_bytes = plan
                    .block_stride
                    .checked_mul(blocks)
                    .ok_or(RefError::Overflow)?;
            }
        }
        Ok(plan)
    }

    pub const fn kind(&self) -> LayoutKind {
        self.kind
    }

    pub const fn row_count(&self) -> usize {
        self.rows
    }

    pub fn field_count(&self) -> usize {
        self.fields.len()
    }

    pub const fn total_bytes(&self) -> usize {
        self.total_bytes
    }

    /// Find one field's offset in the *single* physical store.
    /// Caller is responsible for domain, generation, borrow and lifetime checks.
    pub fn field_offset(&self, row: usize, field_index: usize) -> Result<usize, RefError> {
        if row >= self.rows {
            return Err(RefError::OutOfRange);
        }
        let placement = self.fields.get(field_index).ok_or(RefError::OutOfRange)?;
        let offset = match self.kind {
            LayoutKind::Aos => row
                .checked_mul(self.row_stride)
                .and_then(|n| n.checked_add(placement.base)),
            LayoutKind::Soa => row
                .checked_mul(placement.element_stride)
                .and_then(|n| n.checked_add(placement.base)),
            LayoutKind::Aosoa { lanes } => {
                let block = row / lanes;
                let lane = row % lanes;
                block
                    .checked_mul(self.block_stride)
                    .and_then(|n| n.checked_add(placement.base))
                    .and_then(|n| {
                        lane.checked_mul(placement.element_stride)
                            .and_then(|v| n.checked_add(v))
                    })
            }
        }
        .ok_or(RefError::Overflow)?;
        let end = offset
            .checked_add(self.widths[field_index])
            .ok_or(RefError::Overflow)?;
        if end > self.total_bytes {
            return Err(RefError::OutOfRange);
        }
        Ok(offset)
    }

    /// Return a checked field byte span within a caller-verified backing
    /// allocation. Offsets are arithmetic metadata, never native pointers.
    /// The backing allocation must cover the entire owned store, including
    /// reserved AoSoA tail blocks; an early field fitting by itself is not
    /// sufficient to admit a truncated allocation.
    ///
    /// A valid plan does not prove that the caller owns the backing region:
    /// its actual allocation length, provenance, generation, and borrow
    /// permissions must be authenticated separately.
    pub fn checked_field_span(
        &self,
        row: usize,
        field_index: usize,
        backing_byte_len: usize,
    ) -> Result<std::ops::Range<usize>, RefError> {
        let start = self.field_offset(row, field_index)?;
        // A LayoutPlan describes one complete authoritative store. Do not
        // authorize any field through an allocation smaller than the plan.
        if backing_byte_len < self.total_bytes {
            return Err(RefError::OutOfRange);
        }
        let width = *self.widths.get(field_index).ok_or(RefError::OutOfRange)?;
        let end = start.checked_add(width).ok_or(RefError::Overflow)?;
        if end > backing_byte_len {
            return Err(RefError::OutOfRange);
        }
        Ok(start..end)
    }
}
