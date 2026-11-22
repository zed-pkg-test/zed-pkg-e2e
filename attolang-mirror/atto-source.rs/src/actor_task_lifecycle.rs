//! Trusted-host actor task lifetime ledger (not a source Task compiler/runtime).
//! Actor execution and carrier authenticity MUST be checked by the native
//! scheduler before calling this ledger. No guest closure is executed here.
//! The ledger deliberately never invents a physical quiescence receipt.
use std::collections::BTreeMap;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ActorIncarnation {
    pub id: u64,
    pub generation: u64,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct TaskId(pub u64);

/// Logical replay fence only; the trusted scheduler must independently
/// authenticate the physical actor WAKE turn and the Future producer receipt.
/// Private fields prevent caller construction, not theft of a copied ticket.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct TaskWakeTicket {
    owner: ActorIncarnation,
    task: TaskId,
    generation: u64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TaskStage {
    Ready,
    Running,
    Suspending,
    Suspended,
    Done,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TaskError {
    StaleActor,
    Stopped,
    Capacity,
    ExhaustedIds,
    MissingTask,
    InvalidTransition,
    ParentUnavailable,
    HasLiveChildren,
    NotQuiescent,
    Cancelled,
    ActorBusy,
    WakeRequired,
    InvalidWake,
}
#[derive(Clone, Copy, Debug)]
struct Entry {
    parent: Option<TaskId>,
    stage: TaskStage,
    cancel_requested: bool,
    wake_generation: u64,
}

/// A bounded per-actor logical task ledger. `Done` records still count toward
/// the actor's completion barrier until the runtime explicitly calls `retire`.
/// An awaited Future producer does not hold the actor's physical carrier lease.
pub struct ActorTaskLedger {
    incarnation: ActorIncarnation,
    capacity: usize,
    next_id: u64,
    stopped: bool,
    tasks: BTreeMap<TaskId, Entry>,
}
impl ActorTaskLedger {
    pub fn new(incarnation: ActorIncarnation, capacity: usize) -> Result<Self, TaskError> {
        if incarnation.id == 0 || incarnation.generation == 0 || capacity == 0 || capacity > 1024 {
            return Err(TaskError::InvalidTransition);
        }
        Ok(Self { incarnation, capacity, next_id: 0, stopped: false, tasks: BTreeMap::new() })
    }
    fn check_owner(&self, owner: ActorIncarnation) -> Result<(), TaskError> {
        if owner != self.incarnation { Err(TaskError::StaleActor) } else { Ok(()) }
    }
    pub fn admit_in_authenticated_turn(
        &mut self, owner: ActorIncarnation, parent: Option<TaskId>
    ) -> Result<TaskId, TaskError> {
        self.check_owner(owner)?;
        if self.stopped { return Err(TaskError::Stopped); }
        // Check parent identity and lifecycle before revealing actor task
        // occupancy. A fabricated parent must never become a capacity oracle.
        if let Some(p) = parent {
            let entry = self.tasks.get(&p).ok_or(TaskError::ParentUnavailable)?;
            if entry.cancel_requested || entry.stage != TaskStage::Running {
                // A child may be admitted only while its actual parent owns
                // the actor turn, never by a late Future producer or after
                // that parent has suspended or settled.
                return Err(TaskError::ParentUnavailable);
            }
        }
        if self.tasks.len() >= self.capacity { return Err(TaskError::Capacity); }
        self.next_id = self.next_id.checked_add(1).ok_or(TaskError::ExhaustedIds)?;
        let id = TaskId(self.next_id);
        self.tasks.insert(id, Entry {
            parent, stage: TaskStage::Ready, cancel_requested: false, wake_generation: 0
        });
        Ok(id)
    }
    pub fn start_or_resume(
        &mut self, owner: ActorIncarnation, id: TaskId
    ) -> Result<(), TaskError> {
        self.check_owner(owner)?;
        if self.stopped { return Err(TaskError::Stopped); }
        // Validate identity and lifecycle before observing other running
        // tasks, so a forged TaskId never leaks actor scheduling state.
        let task = self.tasks.get(&id).ok_or(TaskError::MissingTask)?;
        if task.cancel_requested { return Err(TaskError::Cancelled); }
        if task.stage == TaskStage::Suspended { return Err(TaskError::WakeRequired); }
        if task.stage != TaskStage::Ready { return Err(TaskError::InvalidTransition); }
        // Suspended tasks are logical concurrency, not simultaneous
        // mutable execution on two physical carriers for one actor.
        if self.tasks.iter().any(|(other, task)|
            *other != id && matches!(task.stage, TaskStage::Running | TaskStage::Suspending)) {
            return Err(TaskError::ActorBusy);
        }
        self.tasks.get_mut(&id).expect("validated task").stage = TaskStage::Running;
        Ok(())
    }
    /// This is a logical ticket consumption check. Only a physical actor WAKE
    /// callback may call it after checking the scheduler's exact incarnation,
    /// carrier lease and trusted producer ticket. It does not do that itself.
    pub fn resume_from_authenticated_wake(
        &mut self, owner: ActorIncarnation, id: TaskId, ticket: TaskWakeTicket
    ) -> Result<(), TaskError> {
        self.check_owner(owner)?;
        if self.stopped { return Err(TaskError::Stopped); }
        // Reject wrong task/replay before revealing another actor turn state.
        let task = self.tasks.get(&id).ok_or(TaskError::MissingTask)?;
        if task.cancel_requested { return Err(TaskError::Cancelled); }
        if task.stage != TaskStage::Suspended || ticket.owner != owner
            || ticket.task != id || ticket.generation != task.wake_generation {
            return Err(TaskError::InvalidWake);
        }
        if self.tasks.iter().any(|(other, task)|
            *other != id && matches!(task.stage, TaskStage::Running | TaskStage::Suspending)) {
            return Err(TaskError::ActorBusy);
        }
        self.tasks.get_mut(&id).expect("validated task").stage = TaskStage::Running;
        Ok(())
    }
    pub fn suspend(
        &mut self, owner: ActorIncarnation, id: TaskId
    ) -> Result<TaskWakeTicket, TaskError> {
        self.check_owner(owner)?;
        if self.stopped { return Err(TaskError::Stopped); }
        let task = self.tasks.get_mut(&id).ok_or(TaskError::MissingTask)?;
        if task.cancel_requested { return Err(TaskError::Cancelled); }
        if task.stage != TaskStage::Running { return Err(TaskError::InvalidTransition); }
        task.wake_generation = task.wake_generation.checked_add(1)
            .ok_or(TaskError::ExhaustedIds)?;
        // This callback still owns the physical lease until the trusted
        // carrier has returned, not merely until the Future was registered.
        task.stage = TaskStage::Suspending;
        Ok(TaskWakeTicket { owner, task: id, generation: task.wake_generation })
    }
    /// Trusted scheduler-only physical-turn retirement acknowledgement.
    /// Logical Future readiness on an external producer cannot call this.
    pub fn confirm_suspended_turn_returned(
        &mut self, owner: ActorIncarnation, id: TaskId, ticket: TaskWakeTicket
    ) -> Result<(), TaskError> {
        self.check_owner(owner)?;
        let task = self.tasks.get_mut(&id).ok_or(TaskError::MissingTask)?;
        if task.stage != TaskStage::Suspending || ticket.owner != owner
            || ticket.task != id || ticket.generation != task.wake_generation {
            return Err(TaskError::InvalidWake);
        }
        task.stage = TaskStage::Suspended;
        Ok(())
    }
    /// Called only after the task's physical turn has returned.
    pub fn complete_turn(&mut self, owner: ActorIncarnation, id: TaskId) -> Result<(), TaskError> {
        self.check_owner(owner)?;
        let task = self.tasks.get_mut(&id).ok_or(TaskError::MissingTask)?;
        if task.stage != TaskStage::Running { return Err(TaskError::InvalidTransition); }
        task.stage = TaskStage::Done;
        Ok(())
    }
    fn is_descendant_or_self(&self, mut id: TaskId, ancestor: TaskId) -> bool {
        loop {
            if id == ancestor { return true; }
            let Some(parent) = self.tasks.get(&id).and_then(|task| task.parent) else {
                return false;
            };
            id = parent;
        }
    }
    /// Cancellation is a request. It does NOT imply physical completion.
    /// A completed parent's result remains settled, but live children are
    /// still marked for cancellation.
    pub fn request_cancel(
        &mut self, owner: ActorIncarnation, id: TaskId
    ) -> Result<(), TaskError> {
        self.check_owner(owner)?;
        if !self.tasks.contains_key(&id) { return Err(TaskError::MissingTask); }
        let descendants: Vec<_> = self.tasks.keys().copied()
            .filter(|child| self.is_descendant_or_self(*child, id)).collect();
        for child in descendants {
            self.tasks.get_mut(&child).expect("live descendant").cancel_requested = true;
        }
        Ok(())
    }
    pub fn stop(&mut self, owner: ActorIncarnation) -> Result<(), TaskError> {
        self.check_owner(owner)?;
        self.stopped = true;
        for task in self.tasks.values_mut() { task.cancel_requested = true; }
        Ok(())
    }
    /// Scheduler-owned cancellation cleanup callback for never-started or
    /// suspended tasks. An actively running guest turn MUST return first.
    pub fn cleanup_cancelled(
        &mut self, owner: ActorIncarnation, id: TaskId
    ) -> Result<(), TaskError> {
        self.check_owner(owner)?;
        let task = self.tasks.get_mut(&id).ok_or(TaskError::MissingTask)?;
        if !task.cancel_requested ||
            (task.stage != TaskStage::Ready && task.stage != TaskStage::Suspended) {
            return Err(TaskError::NotQuiescent);
        }
        task.stage = TaskStage::Done;
        Ok(())
    }
    /// Must follow proof that this exact task's physical callback/cleanup has
    /// returned. Never infer this from a Promise result or Future settlement.
    pub fn retire(&mut self, owner: ActorIncarnation, id: TaskId) -> Result<(), TaskError> {
        self.check_owner(owner)?;
        let task = self.tasks.get(&id).ok_or(TaskError::MissingTask)?;
        if task.stage != TaskStage::Done { return Err(TaskError::NotQuiescent); }
        if self.tasks.values().any(|entry| entry.parent == Some(id)) {
            return Err(TaskError::HasLiveChildren);
        }
        self.tasks.remove(&id);
        Ok(())
    }
    pub fn is_cancel_requested(&self, id: TaskId) -> Option<bool> {
        self.tasks.get(&id).map(|task| task.cancel_requested)
    }
    pub fn stage(&self, id: TaskId) -> Option<TaskStage> {
        self.tasks.get(&id).map(|task| task.stage)
    }
    pub fn outstanding(&self) -> usize { self.tasks.len() }
    pub fn is_physically_quiescent(&self) -> bool { self.tasks.is_empty() }
}

#[cfg(test)]
mod tests {
    use super::*;
    const OWNER: ActorIncarnation = ActorIncarnation { id: 7, generation: 11 };
    fn ledger(n: usize) -> ActorTaskLedger { ActorTaskLedger::new(OWNER, n).unwrap() }
    #[test]
    fn completed_parent_still_owns_and_cancels_nested_child() {
        let mut l = ledger(8);
        let p = l.admit_in_authenticated_turn(OWNER, None).unwrap();
        l.start_or_resume(OWNER, p).unwrap();
        let c = l.admit_in_authenticated_turn(OWNER, Some(p)).unwrap();
        l.complete_turn(OWNER, p).unwrap();
        assert_eq!(l.retire(OWNER, p), Err(TaskError::HasLiveChildren));
        l.request_cancel(OWNER, p).unwrap();
        assert_eq!(l.stage(p), Some(TaskStage::Done));
        assert_eq!(l.start_or_resume(OWNER, c), Err(TaskError::Cancelled));
        l.cleanup_cancelled(OWNER, c).unwrap();
        l.retire(OWNER, c).unwrap();
        l.retire(OWNER, p).unwrap();
        assert!(l.is_physically_quiescent());
    }
    #[test]
    fn stop_during_await_keeps_done_barrier_closed_until_cleanup() {
        let mut l = ledger(8);
        let t = l.admit_in_authenticated_turn(OWNER, None).unwrap();
        l.start_or_resume(OWNER, t).unwrap();
        let ticket = l.suspend(OWNER, t).unwrap();
        l.confirm_suspended_turn_returned(OWNER, t, ticket).unwrap();
        l.stop(OWNER).unwrap();
        assert_eq!(l.start_or_resume(OWNER, t), Err(TaskError::Stopped));
        assert_eq!(l.admit_in_authenticated_turn(OWNER, None), Err(TaskError::Stopped));
        assert!(!l.is_physically_quiescent());
        l.cleanup_cancelled(OWNER, t).unwrap();
        assert!(!l.is_physically_quiescent());
        l.retire(OWNER, t).unwrap();
        assert!(l.is_physically_quiescent());
    }
    #[test]
    fn running_task_cannot_be_cleanup_retired_by_stop() {
        let mut l = ledger(8);
        let t = l.admit_in_authenticated_turn(OWNER, None).unwrap();
        l.start_or_resume(OWNER, t).unwrap();
        l.stop(OWNER).unwrap();
        assert_eq!(l.cleanup_cancelled(OWNER, t), Err(TaskError::NotQuiescent));
        assert_eq!(l.retire(OWNER, t), Err(TaskError::NotQuiescent));
        l.complete_turn(OWNER, t).unwrap();
        l.retire(OWNER, t).unwrap();
        assert!(l.is_physically_quiescent());
    }
    #[test]
    fn quota_and_generation_fence() {
        let mut l = ledger(1);
        let t = l.admit_in_authenticated_turn(OWNER, None).unwrap();
        assert_eq!(l.admit_in_authenticated_turn(OWNER, None), Err(TaskError::Capacity));
        let stale = ActorIncarnation { id: 7, generation: 12 };
        assert_eq!(l.stop(stale), Err(TaskError::StaleActor));
        assert_eq!(l.start_or_resume(stale, t), Err(TaskError::StaleActor));
        l.start_or_resume(OWNER, t).unwrap();
        l.complete_turn(OWNER, t).unwrap();
        l.retire(OWNER, t).unwrap();
        assert!(l.admit_in_authenticated_turn(OWNER, None).is_ok());
    }
    #[test]
    fn missing_task_identity_precedes_actor_busy_signal() {
        let mut l = ledger(2);
        let running = l.admit_in_authenticated_turn(OWNER, None).unwrap();
        l.start_or_resume(OWNER, running).unwrap();
        let ghost = TaskId(999);
        assert_eq!(l.start_or_resume(OWNER, ghost), Err(TaskError::MissingTask));
        let bogus = TaskWakeTicket {
            owner: OWNER, task: ghost, generation: 1
        };
        assert_eq!(l.resume_from_authenticated_wake(OWNER, ghost, bogus),
            Err(TaskError::MissingTask));
        l.complete_turn(OWNER, running).unwrap();
        l.retire(OWNER, running).unwrap();
    }
    #[test]
    fn parent_must_be_actively_running_to_admit_nested_task() {
        let mut l = ledger(4);
        let parent = l.admit_in_authenticated_turn(OWNER, None).unwrap();
        assert_eq!(l.admit_in_authenticated_turn(OWNER, Some(parent)),
            Err(TaskError::ParentUnavailable));
        l.start_or_resume(OWNER, parent).unwrap();
        let child = l.admit_in_authenticated_turn(OWNER, Some(parent)).unwrap();
        l.start_or_resume(OWNER, child).unwrap_err(); // same mutable actor turn
        let wake = l.suspend(OWNER, parent).unwrap();
        l.confirm_suspended_turn_returned(OWNER, parent, wake).unwrap();
        assert_eq!(l.admit_in_authenticated_turn(OWNER, Some(parent)),
            Err(TaskError::ParentUnavailable));
        l.resume_from_authenticated_wake(OWNER, parent, wake).unwrap();
        l.complete_turn(OWNER, parent).unwrap();
        assert_eq!(l.admit_in_authenticated_turn(OWNER, Some(parent)),
            Err(TaskError::ParentUnavailable));
        l.request_cancel(OWNER, parent).unwrap();
        l.cleanup_cancelled(OWNER, child).unwrap();
        l.retire(OWNER, child).unwrap();
        l.retire(OWNER, parent).unwrap();
        assert!(l.is_physically_quiescent());
    }
    #[test]
    fn wake_replay_wrong_task_and_stale_incarnation_fail_closed() {
        let mut l = ledger(3);
        let a = l.admit_in_authenticated_turn(OWNER, None).unwrap();
        let b = l.admit_in_authenticated_turn(OWNER, None).unwrap();
        l.start_or_resume(OWNER, a).unwrap();
        let first = l.suspend(OWNER, a).unwrap();
        assert_eq!(l.resume_from_authenticated_wake(OWNER, a, first),
            Err(TaskError::InvalidWake));
        l.confirm_suspended_turn_returned(OWNER, a, first).unwrap();
        assert_eq!(l.resume_from_authenticated_wake(OWNER, b, first),
            Err(TaskError::InvalidWake));
        let stale = ActorIncarnation { id: OWNER.id, generation: OWNER.generation + 1 };
        assert_eq!(l.resume_from_authenticated_wake(stale, a, first),
            Err(TaskError::StaleActor));
        l.resume_from_authenticated_wake(OWNER, a, first).unwrap();
        assert_eq!(l.resume_from_authenticated_wake(OWNER, a, first),
            Err(TaskError::InvalidWake));
        let second = l.suspend(OWNER, a).unwrap();
        l.confirm_suspended_turn_returned(OWNER, a, second).unwrap();
        assert_ne!(first, second);
        assert_eq!(l.resume_from_authenticated_wake(OWNER, a, first),
            Err(TaskError::InvalidWake));
        l.resume_from_authenticated_wake(OWNER, a, second).unwrap();
        l.complete_turn(OWNER, a).unwrap();
        l.retire(OWNER, a).unwrap();
        l.start_or_resume(OWNER, b).unwrap();
        l.complete_turn(OWNER, b).unwrap();
        l.retire(OWNER, b).unwrap();
        assert!(l.is_physically_quiescent());
    }
    #[test]
    fn stop_revokes_late_wake_without_forging_physical_retirement() {
        let mut l = ledger(1);
        let t = l.admit_in_authenticated_turn(OWNER, None).unwrap();
        l.start_or_resume(OWNER, t).unwrap();
        let wake = l.suspend(OWNER, t).unwrap();
        l.confirm_suspended_turn_returned(OWNER, t, wake).unwrap();
        l.stop(OWNER).unwrap();
        assert_eq!(l.resume_from_authenticated_wake(OWNER, t, wake),
            Err(TaskError::Stopped));
        assert!(!l.is_physically_quiescent());
        l.cleanup_cancelled(OWNER, t).unwrap();
        assert!(!l.is_physically_quiescent());
        l.retire(OWNER, t).unwrap();
        assert!(l.is_physically_quiescent());
    }
    #[test]
    fn physical_suspend_completion_precedes_resume_and_cleanup() {
        let mut l = ledger(2);
        let a = l.admit_in_authenticated_turn(OWNER, None).unwrap();
        let b = l.admit_in_authenticated_turn(OWNER, None).unwrap();
        l.start_or_resume(OWNER, a).unwrap();
        let ticket = l.suspend(OWNER, a).unwrap();
        assert_eq!(l.stage(a), Some(TaskStage::Suspending));
        assert_eq!(l.start_or_resume(OWNER, b), Err(TaskError::ActorBusy));
        assert_eq!(l.resume_from_authenticated_wake(OWNER, a, ticket),
            Err(TaskError::InvalidWake));
        l.stop(OWNER).unwrap();
        assert_eq!(l.cleanup_cancelled(OWNER, a), Err(TaskError::NotQuiescent));
        assert_eq!(l.retire(OWNER, a), Err(TaskError::NotQuiescent));
        let stale = TaskWakeTicket { generation: ticket.generation + 1, ..ticket };
        assert_eq!(l.confirm_suspended_turn_returned(OWNER, a, stale),
            Err(TaskError::InvalidWake));
        l.confirm_suspended_turn_returned(OWNER, a, ticket).unwrap();
        assert_eq!(l.confirm_suspended_turn_returned(OWNER, a, ticket),
            Err(TaskError::InvalidWake));
        assert_eq!(l.resume_from_authenticated_wake(OWNER, a, ticket),
            Err(TaskError::Stopped));
        l.cleanup_cancelled(OWNER, a).unwrap();
        l.retire(OWNER, a).unwrap();
        l.cleanup_cancelled(OWNER, b).unwrap();
        l.retire(OWNER, b).unwrap();
        assert!(l.is_physically_quiescent());
    }
    #[test]
    fn concurrent_logical_tasks_never_run_mutable_actor_turns_together() {
        let mut l = ledger(2);
        let a = l.admit_in_authenticated_turn(OWNER, None).unwrap();
        let b = l.admit_in_authenticated_turn(OWNER, None).unwrap();
        l.start_or_resume(OWNER, a).unwrap();
        assert_eq!(l.start_or_resume(OWNER, b), Err(TaskError::ActorBusy));
        let ticket = l.suspend(OWNER, a).unwrap();
        assert_eq!(l.start_or_resume(OWNER, b), Err(TaskError::ActorBusy));
        assert_eq!(l.resume_from_authenticated_wake(OWNER, a, ticket),
            Err(TaskError::InvalidWake));
        l.confirm_suspended_turn_returned(OWNER, a, ticket).unwrap();
        l.start_or_resume(OWNER, b).unwrap();
        assert_eq!(l.resume_from_authenticated_wake(OWNER, a, ticket),
            Err(TaskError::ActorBusy));
        l.complete_turn(OWNER, b).unwrap();
        l.retire(OWNER, b).unwrap();
        assert_eq!(l.start_or_resume(OWNER, a), Err(TaskError::WakeRequired));
        l.resume_from_authenticated_wake(OWNER, a, ticket).unwrap();
        l.complete_turn(OWNER, a).unwrap();
        l.retire(OWNER, a).unwrap();
        assert!(l.is_physically_quiescent());
    }

    #[test]
    fn invalid_parent_cannot_probe_capacity_or_consume_a_task_id() {
        let mut l = ledger(1);
        let parent = l.admit_in_authenticated_turn(OWNER, None).unwrap();
        l.start_or_resume(OWNER, parent).unwrap();

        // Even at full capacity, a guessed parent must fail on identity first.
        assert_eq!(
            l.admit_in_authenticated_turn(OWNER, Some(TaskId(u64::MAX))),
            Err(TaskError::ParentUnavailable)
        );
        // A real active parent may observe the capacity bound.
        assert_eq!(
            l.admit_in_authenticated_turn(OWNER, Some(parent)),
            Err(TaskError::Capacity)
        );
        assert_eq!(l.outstanding(), 1);

        l.complete_turn(OWNER, parent).unwrap();
        l.retire(OWNER, parent).unwrap();
        let next = l.admit_in_authenticated_turn(OWNER, None).unwrap();
        assert_eq!(next, TaskId(parent.0 + 1));
    }
}
