//! Domain-relative managed references and physical layout arithmetic.
//! No native pointer or ActorRef is represented by this type.
use std::num::NonZeroU32;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RefMode {
    Compressed32,
    Native,
}

/// Backend-neutral physical representation for the internal managed-reference capability
/// values. Native ABI/FFI pointers are not described by this layout.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ManagedRefLayout {
    mode: RefMode,
    alignment_shift: u8,
}

impl ManagedRefLayout {
    pub fn compressed32(alignment_shift: u8) -> Result<Self, RefError> {
        if alignment_shift > 15 {
            return Err(RefError::BadAlignment);
        }
        Ok(Self {
            mode: RefMode::Compressed32,
            alignment_shift,
        })
    }

    pub const fn native() -> Self {
        Self {
            mode: RefMode::Native,
            alignment_shift: 0,
        }
    }

    pub const fn mode(self) -> RefMode {
        self.mode
    }

    pub const fn mode_name(self) -> &'static str {
        match self.mode {
            RefMode::Compressed32 => "compressed32",
            RefMode::Native => "native",
        }
    }

    pub const fn alignment_shift(self) -> u8 {
        self.alignment_shift
    }

    pub const fn storage_bits(self) -> u32 {
        match self.mode {
            RefMode::Compressed32 => 32,
            RefMode::Native => usize::BITS,
        }
    }

    /// Greatest aligned object-start byte offset representable by compressed32.
    /// Native references are not constrained by this encoding and return None.
    pub const fn max_object_start_offset_bytes(self) -> Option<u64> {
        match self.mode {
            RefMode::Compressed32 => Some(((u32::MAX as u64) - 1) << self.alignment_shift),
            RefMode::Native => None,
        }
    }
}

impl Default for ManagedRefLayout {
    fn default() -> Self {
        Self {
            mode: RefMode::Compressed32,
            alignment_shift: 3,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LayoutKind {
    Aos,
    Soa,
    Aosoa { lanes: usize },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RefError {
    BadAlignment,
    OutOfRange,
    InvalidDomain,
    InvalidLayout,
    Overflow,
}

/// A zero payload means null. All nonzero payloads encode an aligned
/// byte offset divided by 2^shift, plus one. The first object may be at offset 0.
#[repr(transparent)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CompressedRef(u32);

impl CompressedRef {
    pub const NULL: Self = Self(0);

    pub fn is_null(self) -> bool {
        self.0 == 0
    }

    pub fn raw(self) -> u32 {
        self.0
    }

    pub fn from_raw(raw: u32) -> Self {
        Self(raw)
    }
}

/// Heap domain membership must be proven separately by the allocator/borrow checker.
/// base and length are native-width addresses/capacities, not serialized data.
#[derive(Debug, Clone, Copy)]
pub struct Region {
    pub base: usize,
    pub byte_len: usize,
    pub shift: u8,
}

impl Region {
    pub fn new(base: usize, byte_len: usize, shift: u8) -> Result<Self, RefError> {
        if shift > 15 {
            return Err(RefError::BadAlignment);
        }
        let align = 1usize << shift;
        if !base.is_multiple_of(align) {
            return Err(RefError::BadAlignment);
        }
        base.checked_add(byte_len).ok_or(RefError::Overflow)?;
        Ok(Self {
            base,
            byte_len,
            shift,
        })
    }

    pub fn encode_offset(self, offset: usize) -> Result<CompressedRef, RefError> {
        let align = 1usize << self.shift;
        if !offset.is_multiple_of(align) {
            return Err(RefError::BadAlignment);
        }
        if offset >= self.byte_len {
            return Err(RefError::OutOfRange);
        }
        let slot = offset >> self.shift;
        // Reserve zero for null; last representable slot is 2^32-2.
        let slot: u32 = slot.try_into().map_err(|_| RefError::OutOfRange)?;
        let encoded = slot.checked_add(1).ok_or(RefError::OutOfRange)?;
        Ok(CompressedRef(encoded))
    }

    pub fn decode_offset(self, value: CompressedRef) -> Result<Option<usize>, RefError> {
        let Some(slot) = NonZeroU32::new(value.raw()) else {
            return Ok(None);
        };
        // checked_shl only validates the shift *count*; on a 32-bit
        // target it may discard high value bits. Multiply by the proven
        // power-of-two alignment so arithmetic overflow itself fails closed.
        let scale = 1usize << self.shift;
        let offset = ((slot.get() - 1) as usize)
            .checked_mul(scale)
            .ok_or(RefError::Overflow)?;
        if offset >= self.byte_len {
            return Err(RefError::OutOfRange);
        }
        Ok(Some(offset))
    }

    /// Decode only after validating provenance, generation, lifetime and bounds.
    /// Returns an address as usize, not a Rust reference or exposed FFI pointer.
    pub fn decode_address(self, value: CompressedRef) -> Result<Option<usize>, RefError> {
        self.decode_offset(value)?
            .map(|offset| self.base.checked_add(offset).ok_or(RefError::Overflow))
            .transpose()
    }
}

/// AoSoA index arithmetic; a field's byte address is computed by its
/// independent block stride and element stride, never by an AoS cast.
pub fn aosoa_field_offset(
    row: usize,
    lanes: usize,
    block_stride: usize,
    field_base: usize,
    field_element_stride: usize,
) -> Result<usize, RefError> {
    if lanes == 0 || !lanes.is_power_of_two() || block_stride == 0 {
        return Err(RefError::InvalidLayout);
    }
    let block = row / lanes;
    let lane = row % lanes;
    block
        .checked_mul(block_stride)
        .and_then(|n| n.checked_add(field_base))
        .and_then(|n| {
            lane.checked_mul(field_element_stride)
                .and_then(|v| n.checked_add(v))
        })
        .ok_or(RefError::Overflow)
}

/// Bounds-checked AoSoA field byte span for a single logical row.
///
/// Unlike `aosoa_field_offset`, this validates physical field extents,
/// nonoverlapping lanes, logical row count and the actual backing allocation.
/// Returns a byte range only; it never creates a pointer or Rust reference.
/// Caller must still prove ownership, allocation provenance and generation.
pub fn checked_aosoa_field_span(
    row: usize,
    row_count: usize,
    lanes: usize,
    block_stride: usize,
    field_base: usize,
    field_element_stride: usize,
    field_width: usize,
    backing_byte_len: usize,
) -> Result<std::ops::Range<usize>, RefError> {
    // Canonical data-layout-abi-v1 permits tile widths 1..=1024.
    if lanes == 0
        || lanes > 1024
        || !lanes.is_power_of_two()
        || block_stride == 0
        || field_element_stride == 0
        || field_width == 0
        || field_width > field_element_stride
    {
        return Err(RefError::InvalidLayout);
    }

    if row >= row_count {
        return Err(RefError::OutOfRange);
    }

    // Ensure the final lane does not overlap the next block. Checking only
    // the selected lane would permit a malformed layout when another row
    // eventually accesses that same block.
    let block_field_end = (lanes - 1)
        .checked_mul(field_element_stride)
        .and_then(|offset| field_base.checked_add(offset))
        .and_then(|start| start.checked_add(field_width))
        .ok_or(RefError::Overflow)?;
    if block_field_end > block_stride {
        return Err(RefError::InvalidLayout);
    }

    let start = aosoa_field_offset(
        row,
        lanes,
        block_stride,
        field_base,
        field_element_stride,
    )?;
    let end = start.checked_add(field_width).ok_or(RefError::Overflow)?;
    if end > backing_byte_len {
        return Err(RefError::OutOfRange);
    }
    Ok(start..end)
}

/// ECS generational handle; this is an identity, not a memory address.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct EntityHandle {
    pub slot: u32,
    pub generation: u32,
}

impl EntityHandle {
    pub fn is_current(self, current_generation: u32) -> bool {
        self.generation == current_generation
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn managed_ref_layout_is_explicit_and_width_independent() {
        let compressed = ManagedRefLayout::compressed32(3).unwrap();
        assert_eq!(compressed.mode(), RefMode::Compressed32);
        assert_eq!(compressed.storage_bits(), 32);
        assert_eq!(compressed.alignment_shift(), 3);
        assert_eq!(
            compressed.max_object_start_offset_bytes(),
            Some(((u32::MAX as u64) - 1) << 3)
        );

        let native = ManagedRefLayout::native();
        assert_eq!(native.mode(), RefMode::Native);
        assert_eq!(native.storage_bits(), usize::BITS);
        assert_eq!(native.max_object_start_offset_bytes(), None);
        assert_eq!(
            ManagedRefLayout::compressed32(16),
            Err(RefError::BadAlignment)
        );
    }

    #[test]
    fn null_and_first_object() {
        let r = Region::new(0x1000, 4096, 3).unwrap();
        assert_eq!(r.decode_address(CompressedRef::NULL).unwrap(), None);
        let first = r.encode_offset(0).unwrap();
        assert_eq!(first.raw(), 1);
        assert_eq!(r.decode_address(first).unwrap(), Some(0x1000));
    }

    #[test]
    fn round_trip_and_rejections() {
        let r = Region::new(0x1000, 4096, 3).unwrap();
        for offset in (0..4096).step_by(8) {
            assert_eq!(
                r.decode_offset(r.encode_offset(offset).unwrap()).unwrap(),
                Some(offset)
            );
        }
        assert_eq!(r.encode_offset(7), Err(RefError::BadAlignment));
        assert_eq!(r.encode_offset(4096), Err(RefError::OutOfRange));
        assert_eq!(
            r.decode_offset(CompressedRef::from_raw(1000)),
            Err(RefError::OutOfRange)
        );
    }

    #[test]
    fn region_and_overflow() {
        assert_eq!(Region::new(3, 10, 3).unwrap_err(), RefError::BadAlignment);
        assert_eq!(
            Region::new(usize::MAX - 7, 16, 3).unwrap_err(),
            RefError::Overflow
        );
        let r = Region::new(0, usize::MAX, 3).unwrap();
        assert_eq!(
            r.encode_offset((u32::MAX as usize) << 3),
            Err(RefError::OutOfRange)
        );
    }

    #[test]
    fn separate_regions_same_bits_different_addresses() {
        let a = Region::new(0x1000, 4096, 3).unwrap();
        let b = Region::new(0x3000, 4096, 3).unwrap();
        let raw = a.encode_offset(80).unwrap();
        assert_eq!(a.decode_address(raw).unwrap(), Some(0x1050));
        assert_eq!(b.decode_address(raw).unwrap(), Some(0x3050));
        // This does NOT authorize copying raw references between actor domains.
    }

    #[cfg(target_pointer_width = "32")]
    #[test]
    fn compressed_decode_rejects_32_bit_shift_overflow() {
        let r = Region::new(0, usize::MAX, 15).unwrap();
        assert_eq!(
            r.decode_offset(CompressedRef::from_raw(u32::MAX)),
            Err(RefError::Overflow)
        );
    }

    #[test]
    fn aosoa_arithmetic() {
        assert_eq!(aosoa_field_offset(9, 8, 192, 32, 4).unwrap(), 228);
        assert_eq!(
            aosoa_field_offset(0, 0, 192, 32, 4),
            Err(RefError::InvalidLayout)
        );
    }

    #[test]
    fn stale_entity_handle() {
        let h = EntityHandle {
            slot: 42,
            generation: 8,
        };
        assert!(h.is_current(8));
        assert!(!h.is_current(9));
    }
}
