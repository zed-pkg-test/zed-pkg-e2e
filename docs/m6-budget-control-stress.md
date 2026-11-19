# M6 CONTROL coalescing, watchdog and adversarial load — experimental host runtime

Stacked on [M6 selector lifecycle hardening PR #13](https://github.com/atto-lang/atto-source.java/pull/13), which is stacked on M6 [#12](https://github.com/atto-lang/atto-source.java/pull/12) and M5 [#10](https://github.com/atto-lang/atto-source.java/pull/10).

## Code changes

- Terminal STOP and PANIC notices are **one logical in-flight slot per actor incarnation**: repeated STOP calls before/after supervisor delivery do not create a duplicate STOP event. A PANIC still upgrades a queued STOP or delivers a new stronger notice after an already delivered STOP. CONTROL remains strictly before **new** DATA turns; budget exhaustion is never implemented by demoting CONTROL.
- Completed or fenced actor retirement removes an obsolete ready-queue entry, so frequent actor restarts do not accumulate retained scheduler work.
- `AttoM6Runtime(int carrierCount, int maxActors, long turnBudgetMillis)` sets a bounded 1–60000 ms **observation threshold**, default 50 ms for the two-argument constructor.
- A dedicated watchdog thread samples **currently running** callbacks and issues at most one *diagnostic*, non-CONTROL notice per over-budget receive turn, including when the carrier is blocked. `budget_snapshot(ref)` exposes the max completed turn time, settled over-budget turn count, once-per-running-turn diagnostic notice count and current overdue state. `scheduler_snapshot()` exposes bounded ready/CONTROL counts and cumulative watchdog activity.
- Watchdog polls only during active turns and sleeps while all actors are idle; shutdown joins the watchdog before final retirement and forbids internal worker/thread close re-entry.
- No callback is interrupted, force-unwound, migrated mid-turn or preempted by this instrumentation. A callback that does not cooperate can continue arbitrarily long. These tests **do not establish** soft-real-time latency bounds or hard-real-time WCET.

## Required executable regression gates

The existing JVM + GraalVM JDK 25 Native Image entry point now invokes `M6ControlBudgetStressProbe` and requires each new marker to appear identically in both binaries:

1. Block one actor's current receive while a 2,000-request STOP flood arrives across four producers; supervisor dispatches exactly **one** STOP and rejects further DATA admission. The watchdog must produce exactly one diagnostic notice **before** the blocked handler resumes, and another actor must make progress. Three previously accepted DATA turns drain on release.
2. STOP already delivered before a later failed receive must result in a second, stronger PANIC CONTROL notice; abandon pending DATA exactly once, preserve `accepted = completed + failed + abandoned + queued + in_flight`.
3. Four concurrent producers send **100,000** messages to one serial actor while a neighboring actor receives **2,000** messages. Require exactly one active receive per serial actor, conservation of accepted message counts, healthy neighbor progress and zero retained runnable/control tasks after retirement.
4. **48 real NIO pipe readiness-versus-STOP races**: concurrent writers/STOP; all actors retire, with no stale generation admissions, no outstanding held bytes and no leaked registrations.
5. All previous PR #12/#13 tests (NIO TCP loopback, 96 budgeted selected keys, interrupted channel, closed selector calls, panic with saturated DATA, and graceful shutdown) remain mandatory.

## Remaining production gaps

This is **not** actor continuation suspension / `rt cooperate` insertion, compiler-generated `.atto` receiver execution, enforced CPU quotas, bounded scheduling preemption, language-level `select`, hard real-time performance, heap-growth qualification, mailman ACK lease semantics, or LLVM/Wasm/Forge/.NET conformance parity. A separate compiler/runtime milestone must place verified cooperation safe points into emitted loops and await/select continuations. A watchdog only observes and reports a noncooperative task and cannot safely stop it. Watchdog diagnostics are not supervisor priority CONTROL notices.

Verification must use a Git tree and all Git blobs identical to the source draft. If Atto org Actions does not execute steps, use a temporary private alternate-org carrier whose `GITHUB_TOKEN` accesses **only that carrier**, and inspect real JVM/native job logs. Never merge that carrier.
