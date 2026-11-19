# M7: restricted compiled `.atto` safe-point prototype — executed scope

**Parent:** Java M6 [#14](https://github.com/atto-lang/atto-source.java/pull/14). **Shared ABI:** [atto-common-source #18](https://github.com/atto-lang/atto-common-source/pull/18) on top of #17. **Not** the production AttoLang Java or Rust compiler, and no LLVM/Forge/.NET parity is claimed.

## Source-to-runtime pipeline

`fixtures/m7/cooperative_sum.atto` contains a real AttoLang-style actor `on_message(int amount): int` with a typed accumulator, an indexed `while` loop, and an explicit `rt cooperate;` safe point. This first-stage compiler is **`tools/m7_compile_actor_continuation.py`**, separate from the canonical compiler. It parses a strictly bounded, closed-world language subset with no dynamic imports, borrow, `await`, `select`, FFI, or arbitrary statements. Unsupported input fails before artifact generation.

For this accepted subset only, it emits `M7GeneratedActor.java` containing a statically compiled `MessageFrame`, a generated `switch(pc)` machine, long-valued locals, signed i64 `Math.addExact`, and a safepoint that **persists PC before yielding**. Output includes a source SHA256 and a separate manifest. The generated Java is AOT-compiled to real GraalVM Native Image. There is no source interpreter or runtime guest parser.

`src/m7/AttoM7CooperativeRuntime.java` provides up to 32 pooled carriers, one outstanding active receive frame per serial actor, bounded FIFO inbox, generation-fenced actor refs, cooperative reductions/time checks at compile-time safe points, and one eligible ready-queue entry per actor. YIELD retains the **same** accepted message and its frame; the carrier returns to the pool and a subsequent carrier may resume that exact frame. Yield does not settle or re-admit the message. Strict supervisor CONTROL priority, STOP-drain/PANIC-abandon behavior and conservation hold across SUSPENDED and RUNNING turns.

## Executed acceptance gates

`tests/m7/test_restricted_cooperate_compiler.py` must reject unsafe borrow/critical access, unsupported await/select, aliasing locals, malformed grammar, and checked-i64 literal violations. `tests/m7/M7CompiledActorProbe.java` runs the actual generated Java and native binary and requires:

- Compiled actor processing 100,000 loop iterations with a 4-reduction slice budget and >10,000 YIELD/resume cycles; checked sum exactly 4,999,950,000 plus the second accepted message sum 55.
- A neighboring compiled actor advances while the first actor has a suspended in-flight continuation.
- STOP fences new admission but drains accepted suspended/queued work with `accepted = completed + failed + abandoned + queued + inFlight`.
- Reincarnation rejects a stale ActorRef and prevents old continuation authority.
- 32 compiled actors run on four carriers, with three messages per actor and max concurrent receive count 1.
- A separate intentionally faulting host frame tests strict STOP-before-DATA and PANIC escalation after a delivered STOP, exact once-per-turn failed/abandoned accounting and 1,000 redundant STOP coalescing.
- JVM/native output equality and manifest-matched source/code SHA checks.

## Current limitations / follow-up

This restricted compiler is NOT wired into the canonical Java frontend; the existing Rust/Wasm browser frontend rejects executable actor declarations. Full compiler-based ownership/liveness proofs, true `await/select` continuation parking, dynamically sized state frames, multiple receive shapes, direct NIO integration with the existing M6 reactor, user-space parallel critical-state gating, deadline guarantees, watchdog enforcement and hardened untrusted actor isolation remain **NOT EXECUTED/UNSUPPORTED**. The step API is host-callable; a hostile Java frame can ignore the cooperative contract and stall a carrier. A production version must seal the frame ABI behind compiler attestations, prove borrow/critical-loan freedom, and enforce tenant isolation.

A source-org Actions failure before executing steps is infrastructure evidence only. A private SHA-equal Git-tree carrier with real Java JDK25 + Native Image executable logs is required before marking this draft verified.
