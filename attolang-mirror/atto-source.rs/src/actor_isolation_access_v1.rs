//! Host-only, fail-closed actor-local compressed32 allocation authority.
//! NOT a machine-code, tenant-VM, process, or malicious-native-code sandbox.
//! Guest memory lowering MUST route accesses here or through equivalent proven
//! inline checks; arbitrary host pointers must never be exposed to the guest.
use std::collections::HashMap;
use std::sync::Arc;

pub const MAX_COMPRESSED32_SPAN: u64 = (u32::MAX as u64) * 8;

#[derive(Clone, Debug)]
pub struct AllocationGrant {
    issuer: Arc<()>,
    id: u64,
}
#[derive(Debug)]
struct Range {
    start: u64,
    end: u64,
}
pub struct ActorIsolationAccessV1 {
    #[allow(dead_code)]
    tenant: u64,
    #[allow(dead_code)]
    actor: u64,
    #[allow(dead_code)]
    generation: u64,
    span: u64,
    issuer: Arc<()>,
    next_id: u64,
    live: HashMap<u64, Range>,
    retired: bool,
}
impl ActorIsolationAccessV1 {
    pub fn new(tenant: u64, actor: u64, generation: u64, span: u64)
        -> Result<Self, &'static str>
    {
        if tenant == 0 || actor == 0 || generation == 0 ||
            span == 0 || span > MAX_COMPRESSED32_SPAN {
            return Err("invalid actor incarnation or compressed32 span");
        }
        Ok(Self {
            tenant, actor, generation, span, issuer: Arc::new(()),
            next_id: 1, live: HashMap::new(), retired: false,
        })
    }
    pub fn issue_allocation(&mut self, start: u64, length: u64)
        -> Result<AllocationGrant, &'static str>
    {
        if self.retired { return Err("retired actor"); }
        if start % 8 != 0 || length == 0 || start > self.span ||
            length > self.span - start {
            return Err("invalid actor allocation bounds");
        }
        let end = start + length; // safe after subtract-first check
        if self.live.values().any(|r| start < r.end && r.start < end) {
            return Err("overlapping live allocations");
        }
        let id = self.next_id;
        self.next_id = id.checked_add(1).ok_or("grant generation exhausted")?;
        self.live.insert(id, Range { start, end });
        Ok(AllocationGrant { issuer: self.issuer.clone(), id })
    }
    pub fn checked_offset(&self, grant: &AllocationGrant, word: u32, bytes: u64)
        -> Result<u64, &'static str>
    {
        if self.retired { return Err("retired actor"); }
        if !Arc::ptr_eq(&self.issuer, &grant.issuer) {
            return Err("foreign actor or tenant grant");
        }
        let range = self.live.get(&grant.id).ok_or("stale grant")?;
        if word == 0 || bytes == 0 { return Err("null or empty access"); }
        let offset = (u64::from(word) - 1) * 8;
        if offset < range.start || offset > range.end ||
            bytes > range.end - offset {
            return Err("access outside allocation");
        }
        Ok(offset)
    }
    pub fn release(&mut self, grant: &AllocationGrant) -> Result<(), &'static str> {
        if self.retired { return Err("retired actor"); }
        if !Arc::ptr_eq(&self.issuer, &grant.issuer) ||
            self.live.remove(&grant.id).is_none() {
            return Err("foreign or stale grant");
        }
        // Owning allocator must scrub bytes before reallocation.
        Ok(())
    }
    pub fn retire(&mut self) {
        self.retired = true;
        self.live.clear();
        // Caller must fence all turns/FFI pins and scrub physical memory.
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn access_is_allocation_bounded_and_null_rejected() {
        let mut gate = ActorIsolationAccessV1::new(1, 2, 3, 64).unwrap();
        let g = gate.issue_allocation(8, 16).unwrap();
        assert_eq!(gate.checked_offset(&g, 2, 16), Ok(8));
        assert_eq!(gate.checked_offset(&g, 3, 8), Ok(16));
        for (word, bytes) in [(0, 8), (1, 1), (3, 9), (4, 1), (2, 0), (2, u64::MAX)] {
            assert!(gate.checked_offset(&g, word, bytes).is_err());
        }
        assert!(gate.issue_allocation(16, 8).is_err());
        assert!(gate.issue_allocation(1, 8).is_err());
        gate.release(&g).unwrap();
        let reused = gate.issue_allocation(8, 16).unwrap();
        assert!(gate.checked_offset(&g, 2, 8).is_err());
        assert_eq!(gate.checked_offset(&reused, 2, 8), Ok(8));
    }
    #[test]
    fn foreign_tenant_actor_and_restart_grants_are_rejected() {
        let mut first = ActorIsolationAccessV1::new(1, 2, 3, 64).unwrap();
        let g = first.issue_allocation(0, 16).unwrap();
        for (tenant, actor, generation) in [(1, 4, 3), (5, 2, 3), (1, 2, 4)] {
            let other = ActorIsolationAccessV1::new(tenant, actor, generation, 64).unwrap();
            assert!(other.checked_offset(&g, 1, 8).is_err());
        }
        first.retire();
        assert!(first.checked_offset(&g, 1, 8).is_err());
        assert!(first.issue_allocation(0, 8).is_err());
    }
    #[test]
    fn compressed32_last_word_has_no_wraparound() {
        let mut gate = ActorIsolationAccessV1::new(1, 1, 1, MAX_COMPRESSED32_SPAN).unwrap();
        let g = gate.issue_allocation(MAX_COMPRESSED32_SPAN - 8, 8).unwrap();
        assert_eq!(gate.checked_offset(&g, u32::MAX, 8),
                   Ok(MAX_COMPRESSED32_SPAN - 8));
        assert!(gate.checked_offset(&g, u32::MAX, 9).is_err());
        assert!(gate.issue_allocation(MAX_COMPRESSED32_SPAN, 1).is_err());
        assert!(ActorIsolationAccessV1::new(1, 1, 1, MAX_COMPRESSED32_SPAN + 1).is_err());
    }
}
