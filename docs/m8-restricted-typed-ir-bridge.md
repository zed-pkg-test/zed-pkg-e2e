# M8 Java restricted compiler typed safe-point IR bridge

The existing M7 source-to-Java compiler is deliberately a restricted Python pilot, not the canonical AttoLang Java frontend. M8 adds a typed five-operation IR receipt to the **same real compiled .atto source** that GraalVM Native Image executes.

Operations: ENTRY, LOOP_GUARD, CHECKED_ACCUMULATE_AND_INCREMENT, COOPERATE, DONE. The emitted m8-typed-ir.json includes two owned i64 slots, 1..65536 reduction budgets, committed resume_pc 1, and negative borrow and critical-loan claims. The manifest binds source_sha256, java_sha256, and m8_ir_sha256.

Compiler plan validation rejects modified PC, unsupported op sequence, live borrowed state, aliased slots, missing actor generation, or excessive reduction limits. Source syntax remains fail-closed for await/select and other unsupported effects. The Java state machine and strict CONTROL/PANIC callback isolation are inherited from reviewed M7.

Executable evidence requires six Python IR regression tests, checked IR and generated Java artifact digest, and the entire real M7 JVM/GraalVM JDK25 Native Image suite. Source Actions failure with no executed jobs is insufficient; use exact Git tree SHA mirror into private carrier without cross-org checkout tokens.

**Not a full Java compiler integration.** The Java repo at this milestone has a restricted Python AOT compiler, not the canonical Java/GraalVM frontend. The independent Rust browser compiler's new M8 Wasm profile also remains a single-frame, no-mailbox profile. No language await/select, general borrow checker, LLVM/Forge/.NET parity, or hard-real-time guarantees.