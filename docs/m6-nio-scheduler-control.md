# AttoLang M6 experimental NIO + actor scheduling integration

Stacked on Java M5 [PR #10](https://github.com/atto-lang/atto-source.java/pull/10); does not replace the earlier one-thread-per-actor or restricted static AOT pilot. Host Java prototype only: no code generation from canonical `.atto`, no kernel-level malicious tenant sandbox, no hard-real-time guarantee, and no language-level `select` lowering.

- A single Java NIO selector thread owns all `SelectionKey` changes. External registration/cancellation uses bounded command admission and `wakeup()`; this thread **never executes an actor handler**. One-byte direct buffers limit pending input to one byte per source; OP_READ is disabled when mailboxes are full. A sender racing with NIO admission may leave one byte in a bounded held slot, re-attempted only after a carrier dequeues mail.
- MPSC DATA mailboxes have bounded capacity and linearized admission under a shared lock, and carry one `long` per mail in this prototype. Rejection cannot partially commit; this is **not** an affine borrowed-buffer or durable broker ACK implementation.
- A fixed carrier pool runs one `receive` callback per serial actor at a time, with one ready-queue slot per actor, round-robin scheduling of DATA and no carrier-thread pinning. Receiver callbacks may execute on different carriers on successive turns. The native callback uses JVM `Throwable` containment; a production guest needs its own checked error ABI and isolated state arena.
- A separate supervisor thread processes generation-scoped CONTROL events (STOP, PANIC), coalescing to one per actor and upgrading pending STOP to PANIC. Pending CONTROL gates new DATA dispatch; PANIC immediately fences admissions and discards queued DATA with explicit accounting. Running turns settle or fail before retirement; arena reclamation is intentionally outside this prototype.
- Serial actor STOP drains accepted DATA before reporting terminal; panic discards queued work while preserving `accepted = completed + failed + abandoned + queued + in_flight`. Stale actor-generation references cannot be reused across retire/restart. A runtime-wide closing fence prevents new actor creation or sender admission during shutdown.
- Accepted NIO bytes are counted separately from held and explicitly rejected bytes; a STOP racing with a read creates no phantom actor-mail admission. Descriptor cancellation and stale registration revival are fenced. NIO `select()` is **not** the Atto language-level `select` statement.

## Executable tests

```sh
javac -d out src/m6/AttoM6Runtime.java tests/m6/M6NioRuntimeProbe.java
java -cp out M6NioRuntimeProbe
```

The private CI workflow also builds and executes a real GraalVM JDK 25 Native Image with `--no-fallback --gc=epsilon` and compares native/JVM trace markers at the exact same source Git tree. Required scenarios: blocking callback with bounded NIO ingress/backpressure and wakeup, strict CONTROL before queued DATA, panicking actor discard/fail accounting and healthy neighbor, stale generation fencing, four concurrent MPSC producers, one active handler per serial actor, graceful shutdown, concurrent close fencing and idempotent close.

## Remaining production gates

Structured handoff to LLVM `epoll` / `kqueue` / IOCP, socket writes and partial frames, real deadline timers, multi-tenant budgeting and watchdog, scheduler work-stealing with reductions, affine zero-copy mail, event trace v2 instrumentation, bounded buffer pools, OS-backed untrusted tenant isolation, compiled `.atto` `select`, `suspends` and actor-handler codegen. This prototype must stay draft until independently reviewed and exact-head executed tests pass.

**Concurrency scope:** The reactor currently does an O(number of registrations) one-shot rearm scan and admits only one byte per read event. It is bounded by max 256 actors but not a throughput-optimized production design. Sending uses a shared monitor and does not guarantee a wall-clock upper bound; no hard-real-time or lock-free claim is justified.
