//! Bounded byte arena for future actor-owned, non-tracing memory.
//!
//! This is a host substrate, not a compiled guest heap or an actor borrow
//! checker. The arena reserves storage once at construction and uses a bump
//! cursor thereafter. All handles are opaque, region-specific and epoch-bound.
//! No handle exposes an address or grants cross-actor send authority.

use std::sync::atomic::{AtomicUsize, Ordering};

/// Guard against accidental unbounded host allocations in this prototype.
pub const MAX_ARENA_BYTES: usize = 16 * 1024 * 1024;
pub const MAX_OFFSET_ALIGNMENT: usize = 4096;

static NEXT_ARENA_ID: AtomicUsize = AtomicUsize::new(1);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ArenaError {
    InvalidCapacity,
    InvalidAllocation,
    InvalidAlignment,
    OutOfSpace,
    ArithmeticOverflow,
    IdExhausted,
    EpochExhausted,
    ForeignArena,
    StaleHandle,
    InvalidHandle,
    Retired,
}

/// An offset/length lease, not a native pointer or transferable actor token.
/// The fields are private: only the owning arena can mint handles using safe
/// Rust. Copying a handle does not grant a simultaneous mutable borrow.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ArenaHandle {
    arena_id: usize,
    epoch: u64,
    offset: usize,
    len: usize,
}

impl ArenaHandle {
    pub const fn byte_len(self) -> usize {
        self.len
    }
}

/// One fixed backing allocation; no tracing, moving or per-object allocation.
/// Exclusive mutations are enforced by Rust's mutable-reference rules. Actor
/// identity and scheduling permissions still require a separate runtime gate.
#[derive(Debug)]
pub struct OwnerArena {
    bytes: Box<[u8]>,
    used: usize,
    id: usize,
    epoch: u64,
    retired: bool,
}

impl OwnerArena {
    pub fn new(capacity: usize) -> Result<Self, ArenaError> {
        if capacity > MAX_ARENA_BYTES {
            return Err(ArenaError::InvalidCapacity);
        }
        let mut bytes = Vec::new();
        bytes
            .try_reserve_exact(capacity)
            .map_err(|_| ArenaError::OutOfSpace)?;
        bytes.resize(capacity, 0);
        // Never wrap the ID. A surviving handle cannot be mistaken for a
        // handle from a newly constructed arena even if memory is reused.
        let id = NEXT_ARENA_ID
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |n| n.checked_add(1))
            .map_err(|_| ArenaError::IdExhausted)?;
        Ok(Self {
            bytes: bytes.into_boxed_slice(),
            used: 0,
            id,
            epoch: 1,
            retired: false,
        })
    }

    pub fn is_retired(&self) -> bool {
        self.retired
    }

    pub fn capacity(&self) -> usize {
        self.bytes.len()
    }

    pub fn used_bytes(&self) -> usize {
        self.used
    }

    pub fn remaining_bytes(&self) -> usize {
        self.bytes.len() - self.used
    }

    /// Reserve a positive contiguous byte region at an aligned *offset*.
    /// This does not guarantee native pointer alignment of the backing Vec,
    /// and cannot be cast to a Rust struct or FFI pointer.
    pub fn allocate(&mut self, len: usize, alignment: usize) -> Result<ArenaHandle, ArenaError> {
        if self.retired {
            return Err(ArenaError::Retired);
        }
        if len == 0 {
            return Err(ArenaError::InvalidAllocation);
        }
        if alignment == 0
            || alignment > MAX_OFFSET_ALIGNMENT
            || !alignment.is_power_of_two()
        {
            return Err(ArenaError::InvalidAlignment);
        }
        let start = self
            .used
            .checked_add(alignment - 1)
            .map(|n| n & !(alignment - 1))
            .ok_or(ArenaError::ArithmeticOverflow)?;
        let end = start
            .checked_add(len)
            .ok_or(ArenaError::ArithmeticOverflow)?;
        if end > self.bytes.len() {
            return Err(ArenaError::OutOfSpace);
        }
        self.used = end;
        Ok(ArenaHandle {
            arena_id: self.id,
            epoch: self.epoch,
            offset: start,
            len,
        })
    }

    fn range(&self, handle: ArenaHandle) -> Result<std::ops::Range<usize>, ArenaError> {
        if self.retired {
            return Err(ArenaError::Retired);
        }
        if handle.arena_id != self.id {
            return Err(ArenaError::ForeignArena);
        }
        if handle.epoch != self.epoch {
            return Err(ArenaError::StaleHandle);
        }
        let end = handle
            .offset
            .checked_add(handle.len)
            .ok_or(ArenaError::InvalidHandle)?;
        if handle.len == 0 || end > self.used {
            return Err(ArenaError::InvalidHandle);
        }
        Ok(handle.offset..end)
    }

    /// A borrowed byte slice cannot outlive or race with reset/allocate.
    /// No native pointer is exposed.
    pub fn read(&self, handle: ArenaHandle) -> Result<&[u8], ArenaError> {
        let range = self.range(handle)?;
        Ok(&self.bytes[range])
    }

    pub fn write(&mut self, handle: ArenaHandle) -> Result<&mut [u8], ArenaError> {
        let range = self.range(handle)?;
        Ok(&mut self.bytes[range])
    }

    /// Deterministic bulk reclamation at the owner's explicit quiescent point.
    /// All allocated bytes, including padding, are reinitialized for reuse.
    /// This is not cryptographic secure erasure. Old epochs never revalidate.
    pub fn reset(&mut self) -> Result<(), ArenaError> {
        if self.retired {
            return Err(ArenaError::Retired);
        }
        let next_epoch = self.epoch.checked_add(1).ok_or(ArenaError::EpochExhausted)?;
        self.bytes[..self.used].fill(0);
        self.used = 0;
        self.epoch = next_epoch;
        Ok(())
    }

    /// Permanently retire the owned arena after proven quiescence. Subsequent
    /// allocations, reads, writes and resets all fail, including stale handles.
    /// Repeated retirement is harmless and does not reopen access.
    pub fn retire(&mut self) {
        if !self.retired {
            self.bytes.fill(0);
            self.used = 0;
            self.retired = true;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exhausted_epoch_fails_without_releasing_handles() {
        let mut arena = OwnerArena::new(8).unwrap();
        let handle = arena.allocate(4, 1).unwrap();
        arena.epoch = u64::MAX;
        assert_eq!(arena.reset(), Err(ArenaError::EpochExhausted));
        assert_eq!(arena.used_bytes(), 4);
        // An old-generation handle still cannot read a new epoch.
        assert_eq!(arena.read(handle), Err(ArenaError::StaleHandle));
    }

    #[test]
    fn internal_handle_bounds_are_checked_defensively() {
        let mut arena = OwnerArena::new(8).unwrap();
        let good = arena.allocate(4, 1).unwrap();
        let mut forged = good;
        forged.len = usize::MAX;
        assert_eq!(arena.read(forged), Err(ArenaError::InvalidHandle));
        assert_eq!(arena.read(good).unwrap().len(), 4);
    }
}
