# M7 callback isolation and PANIC quiescence audit

**Scope:** experimental Java/GraalVM restricted `.atto` actor compiler/runtime stacked on source [PR #15](https://github.com/atto-lang/atto-source.java/pull/15). This does NOT certify canonical AttoLang Java/Rust/LLVM lowering, hard real time, untrusted-tenant isolation, or borrow-checking in general programs.

## Fixed hazards

1. **Guest frame construction under scheduler-wide monitor:** previous M7 `actor.factory.new_frame(message)` ran inside `synchronized(gate)`. A slow or reentrant guest factory could freeze supervisor CONTROL admission and block healthy actor sends/spawns/metrics. The carrier now reserves the accepted message under the gate (inFlight=true), releases the gate, invokes the factory, and settles its exact reserved turn after physically quiescing. On error the reserved turn becomes FAILED, the remaining queue is ABANDONED, and PANIC CONTROL is coalesced. No nested guest callbacks run under the gate.
2. **Guest result computation under scheduler-wide monitor:** previous `frame.result()` ran inside the gate and could starve all neighbors. Both `frame.advance()` and `frame.result()` run without the gate; the computed scalar result is committed atomically under the gate only after the callback finishes. Overflow of aggregate results becomes a FAILED message followed by PANIC, not a partial completion.
3. **Mutable continuation PC introspection under the gate:** previous `snapshot()` called guest `frame.resume_pc()` when suspended. An adversarial or unexpectedly slow callback could block all actors and CONTROL. PC is sampled by the carrier **outside the gate** after YIELD and validated in 0..4096; only the committed integer is stored under the gate. Running frames report PC=-2 without consulting user code; settled frames report -1.
4. **PANIC versus in-flight work:** an accepted message is now independently tracked with an `inFlight` reservation while its factory is executing, a carrier is running, or its continuation is suspended. `request_panic()` immediately fences new DATA admission and submits priority CONTROL. The supervisor cancels a fully suspended frame and settles it as ABANDONED without using a carrier. When PANIC races a running factory/advance/result/PC callback, the runtime waits until it actually returns before abandoning the turn; **it never frees/invalidates a running frame's memory early**. A real callback failure still settles FAILED and coalesces PANIC.
5. **Conservation:** `accepted == completed + failed + abandoned + queued + (inFlight ? 1 : 0)` holds before/during/after frame construction, YIELD, and PANIC. Graceful STOP continues draining previously accepted work and new sends return CLOSED or STALE.

## Executed acceptance tests

The Java/GraalVM native CI gate now executes `tests/m7/M7CallbackIsolationAuditProbe.java` alongside all existing M7 compiled-`.atto` positive/negative tests. Its four mandatory markers mean:

- `M7_AUDIT_BLOCKED_FRAME_FACTORY_LOCK_ISOLATION=PASS`: a factory deliberately blocks; supervisor STOP, snapshots, neighbor actor spawn/sends and healthy receive must proceed without waiting for the factory; reserved accepted DATA remains conserved.
- `M7_AUDIT_BLOCKED_RESULT_NO_GLOBAL_MONITOR=PASS`: `frame.result()` deliberately blocks; CONTROL and healthy actor progress continue; the original accepted frame eventually settles.
- `M7_AUDIT_PANIC_SUSPENDED_FRAME_CONSERVATION=PASS`: a single carrier is blocked by an unrelated actor while the target has a genuine *SUSPENDED* frame; PANIC retires it without resuming the frame or losing accepted mail.
- `M7_AUDIT_PANIC_RUNNING_PC_QUISCES=PASS`: PANIC arrives while `frame.resume_pc()` is physically running; supervisor still dispatches, but retirement waits for the callback to return, and that frame never resumes.

The prior compiled guest sum/PC, 32 actors/4 carriers, STOP/PANIC priority, reincarnation fencing, 9 fail-closed compiler tests, source digest and JVM/native output parity must **also** pass. Require a real Native Image ELF on a private exact-SHA-tree carrier when source Actions minutes are exhausted. Merely discovering the tests or compiling source is **not evidence of pass**.

## Remaining production work

Compiler-only seals for FrameFactory authority, embedded continuation ownership/loan tracking, `await/select` parking, consistent cancellation while long-running callbacks do I/O, true enforced CPU quotas, native LLVM and Rust/Wasm parity and long-running fault-injection/ASan remain outstanding. A host Java callback can ignore the safe point and still monopolize its own carrier; this audit only ensures it cannot monopolize the **scheduler lock**.
