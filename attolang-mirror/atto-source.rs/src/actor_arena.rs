//! Fixed-capacity, actor-owned slots with generation-fenced identities.
//!
//! Operations do not allocate internally or use a garbage collector. This is
//! not a complete actor heap, borrow checker, secure capability, or allocator
//! for arbitrary graphs. Values of T may own allocations outside the arena.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ArenaError {
    InvalidOwner,
    InvalidArena,
    CapacityTooLarge,
    CapacityExhausted,
    WrongOwner,
    WrongArena,
    WrongIncarnation,
    InvalidSlot,
    StaleGeneration,
    Vacant,
}

/// An opaque identity, never a native pointer or a serialized actor capability.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ArenaHandle {
    actor_id: u64,
    incarnation: u64,
    arena_id: u64,
    slot: usize,
    generation: u64,
}

impl ArenaHandle {
    pub fn actor_id(self) -> u64 {
        self.actor_id
    }

    pub fn incarnation(self) -> u64 {
        self.incarnation
    }

    pub fn arena_id(self) -> u64 {
        self.arena_id
    }

    pub fn slot(self) -> usize {
        self.slot
    }

    pub fn generation(self) -> u64 {
        self.generation
    }
}

#[derive(Debug)]
struct Slot<T> {
    value: Option<T>,
    generation: u64,
}

/// A zero-GC slot store with one authoritative copy of each value.
/// Every arena in an actor incarnation requires a distinct, non-reused ID.
///
/// No global registry or runtime memory allocation is used by this structure.
/// A supervisor must allocate unique, nonzero actor IDs, incarnation IDs and
/// arena IDs, and never reuse a full identity triple while handles may exist.
/// Memory isolation, FFI provenance, scheduling and cross-actor sharing are
/// enforced by future runtime contracts, not by these identifiers alone.
/// Limit fixed slot count and inline bytes even before the allocator is integrated.
pub const MAX_ARENA_SLOTS: usize = 4096;
pub const MAX_ARENA_INLINE_BYTES: usize = 4 * 1024 * 1024;

#[derive(Debug)]
pub struct FixedActorArena<T, const N: usize> {
    actor_id: u64,
    incarnation: u64,
    arena_id: u64,
    slots: [Slot<T>; N],
    live: usize,
}

impl<T, const N: usize> FixedActorArena<T, N> {
    pub fn new(actor_id: u64, incarnation: u64, arena_id: u64) -> Result<Self, ArenaError> {
        if actor_id == 0 || incarnation == 0 {
            return Err(ArenaError::InvalidOwner);
        }
        if arena_id == 0 {
            return Err(ArenaError::InvalidArena);
        }
        let inline_bytes = std::mem::size_of::<Slot<T>>()
            .checked_mul(N)
            .ok_or(ArenaError::CapacityTooLarge)?;
        if N > MAX_ARENA_SLOTS || inline_bytes > MAX_ARENA_INLINE_BYTES {
            return Err(ArenaError::CapacityTooLarge);
        }
        Ok(Self {
            actor_id,
            incarnation,
            arena_id,
            slots: std::array::from_fn(|_| Slot {
                value: None,
                generation: 1,
            }),
            live: 0,
        })
    }

    pub fn len(&self) -> usize {
        self.live
    }

    pub fn is_empty(&self) -> bool {
        self.live == 0
    }

    pub fn capacity(&self) -> usize {
        N
    }

    /// Move a value into one free slot. A failed admission returns the original
    /// value without dropping or consuming caller ownership. Retired generations
    /// are never reused, preventing stale-handle resurrection by wraparound.
    pub fn try_insert(&mut self, value: T) -> Result<ArenaHandle, (ArenaError, T)> {
        for (index, slot) in self.slots.iter_mut().enumerate() {
            if slot.value.is_none() && slot.generation != u64::MAX {
                slot.value = Some(value);
                self.live += 1;
                return Ok(ArenaHandle {
                    actor_id: self.actor_id,
                    incarnation: self.incarnation,
                    arena_id: self.arena_id,
                    slot: index,
                    generation: slot.generation,
                });
            }
        }
        Err((ArenaError::CapacityExhausted, value))
    }

    fn validate(&self, handle: ArenaHandle) -> Result<usize, ArenaError> {
        if handle.actor_id != self.actor_id {
            return Err(ArenaError::WrongOwner);
        }
        if handle.incarnation != self.incarnation {
            return Err(ArenaError::WrongIncarnation);
        }
        if handle.arena_id != self.arena_id {
            return Err(ArenaError::WrongArena);
        }
        let slot = self.slots.get(handle.slot).ok_or(ArenaError::InvalidSlot)?;
        if handle.generation != slot.generation {
            return Err(ArenaError::StaleGeneration);
        }
        if slot.value.is_none() {
            return Err(ArenaError::Vacant);
        }
        Ok(handle.slot)
    }

    pub fn get(&self, handle: ArenaHandle) -> Result<&T, ArenaError> {
        let index = self.validate(handle)?;
        self.slots[index].value.as_ref().ok_or(ArenaError::Vacant)
    }

    pub fn get_mut(&mut self, handle: ArenaHandle) -> Result<&mut T, ArenaError> {
        let index = self.validate(handle)?;
        self.slots[index].value.as_mut().ok_or(ArenaError::Vacant)
    }

    /// Return ownership to the caller. No delayed collector or moved-object
    /// pointer is involved. The slot generation advances before reuse.
    pub fn take(&mut self, handle: ArenaHandle) -> Result<T, ArenaError> {
        let index = self.validate(handle)?;
        let slot = &mut self.slots[index];
        let value = slot.value.take().ok_or(ArenaError::Vacant)?;
        slot.generation = slot.generation.saturating_add(1);
        self.live -= 1;
        Ok(value)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generation_exhaustion_retires_slot_instead_of_wrapping() {
        let mut arena = FixedActorArena::<u64, 1>::new(10, 1, 1).unwrap();
        arena.slots[0].generation = u64::MAX - 1;
        let handle = arena.try_insert(42).unwrap();
        assert_eq!(handle.generation(), u64::MAX - 1);
        assert_eq!(arena.take(handle), Ok(42));
        assert_eq!(arena.slots[0].generation, u64::MAX);
        assert_eq!(arena.try_insert(7), Err((ArenaError::CapacityExhausted, 7)));
    }

    #[test]
    fn zero_capacity_fails_closed() {
        let mut arena = FixedActorArena::<u8, 0>::new(1, 1, 1).unwrap();
        assert_eq!(arena.try_insert(4), Err((ArenaError::CapacityExhausted, 4)));
        assert_eq!(arena.len(), 0);
    }
}
