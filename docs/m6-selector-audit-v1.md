# M6 NIO scheduler hardening: selected-key fairness and reactor lifecycle

**Scope:** experimental Java/GraalVM host-side actor runtime stacked on [M6 #12](https://github.com/atto-lang/atto-source.java/pull/12), which is stacked on [M5 #10](https://github.com/atto-lang/atto-source.java/pull/10). These tests do not certify the `.atto` compiler, language-level `select`, zero-GC behavior, or hard real time.

## Confirmed source risks

1. The M6 selector dispatched at most 64 keys per wakeup, leaving additional selected keys in the selected-key set. A subsequent `Selector.select()` can return zero despite the leftover keys: its return value reflects **newly selected** events, not the size of the retained selected set. The old `if (n > 0)` gate could therefore indefinitely strand ready descriptors. M6 now drains preexisting selected keys before sleeping and processes the selected set regardless of the select return count.
2. `SelectionKey.interestOps()` and `isReadable()` can throw `CancelledKeyException` when an application closes a registered channel concurrently. This should detach only the relevant registration and close the channel, not crash the reactor and block unrelated mail.
3. After the reactor has stopped, `cancel_read()` could still enqueue work and wait up to five seconds for a nonexistent selector thread to complete it. All registration, cancellation and snapshot submissions are now fenced under the shared shutdown lock and fail promptly once shutdown begins.

## Added adversarial executed gates

The new `tests/m6/M6SelectorLifecycleAuditProbe.java` runs within the existing Java/JDK25 GraalVM Native Image probe and checks:

- **96 prefilled, concurrently registered real NIO pipes**, versus the reactor's per-pass budget of 64. All 96 distinct actors must receive their one byte exactly once. No phantom admission, leftover held byte, dead actor, or stalled registration.
- Closing a registered channel externally must not prevent another actor's actual pipe readiness from delivering a byte.
- After `close()`, `cancel_read`, `register_read`, and `reactor_snapshot` reject in under one second, never awaiting a stopped reactor. Repeated global close remains idempotent.
- Existing M6 regression cases continue: a real TCP loopback and EOF cleanup; mailbox capacity 3 full while PANIC is reported through separate CONTROL; 4-producer MPSC 1000 turns with maximum one concurrent receive per serial actor; shutdown and stale-generation fencing.

`ci/m6/verify-nio-runtime-native.sh` compiles both probe classes and requires **all previous markers and all three new audit markers** from both real JVM and GraalVM Native Image executions, asserting byte-for-byte matching marker streams.

## Remaining limits

This is still a single selector thread, bounded but O(registrations) interest-rearm scan, one-byte direct I/O buffers, synchronized MPSC mailboxes, and host callback execution. A default 250 ms selector timeout is not an acceptable hard-real-time bound. Production readiness also requires cancellation in-flight race exploration, interrupt-safe carrier supervision, wakeup latency measurements, counted buffer ownership leases, read/write partial frame handling, real `.atto` receiver lowering, and independent LLVM `epoll`/IOCP parity.

A passing private runner must show exact source commit, exact Git tree SHA, exact copied Git blob SHAs, real executed native binary and nonzero completed test steps. Merging the temporary carrier PR is forbidden.
