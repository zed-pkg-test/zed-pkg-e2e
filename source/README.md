> **AI agent instructions (organization-wide):** Before working on code, issues, or pull requests, read and follow the [organization `AGENTS.md`](https://github.com/atto-lang/.github/blob/main/AGENTS.md), including its **merge-first stealth-development policy**. Honor any repository-local `AGENTS.md`, explicit holds, and GitHub-enforced rules.

# atto-source.forge

Independent AttoLang execution engine with JIT, AOT, and native runtime infrastructure.

## Naming and specification migration

This is the canonical [AttoLang backend repository](https://github.com/atto-lang/atto-source.forge). Use `.atto` for portable AttoLang source and update local remotes to `https://github.com/atto-lang/atto-source.forge.git`; do not use historical repository links or naming in new examples.

- **Normative grammar and semantics:** [atto-spec](https://github.com/atto-lang/atto-spec). Check the current versioned contracts for ownership operations (`borrow!`, `take!`, `share!`, `copy!`, `copy!!`), implicit argument borrowing, `lt!` binders, `eq`/`neq` value equality, `susp` effects and file/module `define const`. These are *specification targets*, not claims of executable support in this backend.
- **Positive and negative guest execution evidence:** [atto-platform-conformance](https://github.com/atto-lang/atto-platform-conformance) and [atto-compilers-parity-testing](https://github.com/atto-lang/atto-compilers-parity-testing). Report `passed`, `failed`, `unsupported` or `not executed`, and pin exact compiler, source tree, target and toolchain.
- **Portable shared sources and target constraints:** [atto-common-source](https://github.com/atto-lang/atto-common-source) and [atto-target-profiles](https://github.com/atto-lang/atto-target-profiles). Keep backend-specific gaps explicit, and do not infer guest parity from host tests or zero-step CI runs.

## Scope

This repository is the AttoLang ecosystem home for:

- Independent runtime semantics, JIT/AOT compilation experiments, actors, and backend conformance.
- Compatibility with the [canonical language specification](https://github.com/atto-lang/atto-spec) where applicable.
- Evidence-based tests, benchmark results, and cross-backend conformance reporting.

## Architecture and compatibility

AttoLang is designed around actors, explicit ownership, and portable concurrency semantics. Backend-specific optimizations must not silently change the shared language contract. Runtime capabilities, target restrictions, and unsupported features should be documented explicitly.

## Building and testing

Consult the checked-in source tree and CI workflows for verified commands and supported targets. Add target-specific build instructions and exact-head execution results as they become available; do not treat a passing host-unit test as proof of guest-language conformance.

## Project status

This README describes the repository's intended responsibilities. Feature support and release readiness must be established from implementation, tests, and CI evidence rather than this description alone.

## Related repositories

- [AttoLang organization](https://github.com/atto-lang)
- [Language specification](https://github.com/atto-lang/atto-spec)
- [Platform conformance](https://github.com/atto-lang/atto-platform-conformance)
- [Commercial infrastructure](https://github.com/atto-co)

## Checked i64 native primitive (preview)

`include/atto/checked_i64.hpp` provides portable C++20 `checked_add`, `checked_sub`, and `checked_mul` returning `std::optional<int64_t>` on overflow, without signed-overflow UB or heap allocation. The unsigned-magnitude multiplication path handles `INT64_MIN * -1` explicitly.

Build the independent primitive tests with `cmake -S . -B build -DATTO_FORGE_SANITIZERS=ON && cmake --build build && ctest --test-dir build --output-on-failure`. CI exercises GCC and Clang with ASan/UBSan. These are native library tests only: no Forge compiler, actor runtime, AOT code emission, or Atto guest execution has been implemented or verified by this PR.

## Repository ownership and contribution routing

**Belongs here:** Independent Forge execution backend, native code generation, runtime ABI, actor/scheduler integration, AOT/JIT experiments and backend conformance receipts.

**Belongs elsewhere:** Other backends' source and canonical language definitions belong in atto-source.* and atto-spec; common contracts belong in atto-common-source.

**Security and portability boundary:** Target-specific JIT is optional and governed; isolate untrusted tenant capabilities, bound heap/stack/fuel and distinguish emitted native machine code from executed AttoLang guests.

AttoLang is the canonical project identity. Use `.atto` source, `atto-*` package and repository names, and the active AttoLang CLI contract for current documentation. Do not duplicate normative semantics from [atto-spec](https://github.com/atto-lang/atto-spec).

**Evidence gate:** record exact compiler/runtime head and tree, target/toolchain, nonzero executed steps, positive and negative guest results, and explicit passed/failed/unsupported/not-executed states. Host-only CI and `steps=[]` do not establish guest support. README scope is not a feature-completeness claim.

## Native ownership host-reference model (preview)

The header-only C++20 include/atto/ownership_v1.hpp provides a native
ownership model with **concrete** data-structure behavior, not a parsed Atto
compiler or generated guest bytecode:

- UniqueGraph performs bounded whole-graph validation, iterative cycle
  traversal, duplicate/missing-node rejection and value-allocated deep copy
  preserving internal topology while allocating independent nested buffers.
- Consuming freeze publishes an immutable graph with unique owner authority.
  Shared readers only obtain const graph views. Weak handles perform atomic
  shared_ptr lifetime claims plus domain/generation and retirement checks.
- Pinned<T> keeps its heap pointee at a stable address across handle and
  thread moves; OwnedResource<T>::drop_now() uses unique ownership and RAII
  to release exactly once. The source borrow checker still owns loan rules.

The tests/ownership_v1.cpp binary verifies native cycles, copy independence,
unsafe graph rejection, weak-upgrade/retire races, stale generation, frozen
owner move replacement, pinned address stability and double-release safety.

The audited native host API now rejects *nested* pointer-bearing read
snapshots (not just raw pointers), constructs pinned pointees in place rather
than moving a previously constructed potentially self-referential object,
and requires noexcept destruction for explicit drop. Snapshot callbacks
are fail-closed for nondefault vector allocators (including pmr vectors)
because their backing resource lifetime cannot be certified. Host callback bodies
remain trusted C++ code; this API does not sandbox arbitrary pointer capture.

The tests/ownership_stress_v1.cpp binary adds deterministic cyclic graph
cases at exact node/edge/byte budget boundaries, independent clone mutation,
32 rounds of 4-thread weak upgrade/retire races, and compile-time rejection
checks for unsafe snapshots and movable self-referential pin inputs.

Exact locally executed Git blob identities:
- include/atto/ownership_v1.hpp: 0050651637e265a6e660af77f302e6a9f955e3e8
- tests/ownership_v1.cpp: 5b77e09cec211164276cf46c89a1382c7f33465c
- tests/ownership_stress_v1.cpp: c8bae893835b05f211274e0cb2ecfabb74b77a45

All four native combinations passed locally: GCC and Clang, each running
both executables under -std=c++20 -O1 -g -Wall -Wextra -Werror -pedantic
-fsanitize=address,undefined -fno-omit-frame-pointer -pthread;
ASAN_OPTIONS=detect_leaks=1. Tests are assertions compiled without NDEBUG,
including Release CMake builds. The GitHub Actions runner failure is separate
and must not be reported as passing CI.

This establishes **native host-model behavior only**, not Forge guest IR lowering, actor memory isolation, or executed AttoLang syntax. Related frontend/spec/conformance branches
are in atto-source.rs#344, atto-borrow-checker.rs#43, atto-spec#92,
and atto-platform-conformance#76.

## Restricted native scalar compiled guest (stage 0)

Build CMake and invoke `build/forge_scalar fixtures/scalar-v1/arithmetic.atto -o /tmp/atto-scalar.cpp`,
then compile generated native C++ with `c++ -std=c++20 -I include /tmp/atto-scalar.cpp -o /tmp/atto-scalar`.
The actual guest binary prints `34`. Signed overflow emits `E_OVERFLOW` and exits 70;
division by zero emits `E_DIVZERO` and exits 71.
The C++ frontend and test harness contain no Python. The CMake integration test
compiles and executes four source fixtures on GCC and Clang and denies malformed
or over-budget sources while removing a stale output artifact.

Scope: one scalar main function with literals, parentheses, unary arithmetic
and checked i64 operators. This is not a general Forge JIT/AOT compiler, actor
runtime, library/package system or cross-backend parity proof.

Restricted scalar arithmetic additionally supports signed bitwise `&`, `|`, `^`, and unary `~`, with precedence `~` > multiplicative > additive > `&` > `^` > `|`. Bit patterns are preserved via C++20 `std::bit_cast` rather than implementation-defined signed casts.

Restricted scalar shift syntax: `<<<` bitwise left, `>>>` zero-fill right, `ashr` sign-preserving right, with signed shift counts masked to low six bits. `<<` and `>>` are reserved for channel operations. The shift tier is left associative and between addition and bitwise AND.

Restricted decimal scalar literals permit leading zeros in the signed `INT64_MIN` representation (e.g. `-0009223372036854775808`); this passes the same bounded decimal validator as other integers. Out-of-range positive or negative literals are rejected.

The native Forge tests compile and execute an additional 29-case `.atto` scalar conformance corpus, including signed arithmetic, bitwise precedence, masked shifts, INT64_MIN, overflow and division-by-zero exits. When `ATTO_FORGE_SANITIZERS=ON`, **generated guest binaries** themselves are built under ASan/UBSan, as well as the Forge frontend. This validates only the restricted stage-0 compiled scalar subset, not the full Forge JIT/AOT or actor runtime.

The native Forge CLI now reports sanitized parse codes (`E_PARSE_LEXICAL`, `E_PARSE_SYNTAX`, `E_PARSE_RANGE`, `E_PARSE_DEPTH`, `E_PARSE_NODES`) with source byte offsets, without echoing source text. Invalid source removes stale emitted artifacts and exits 2.

Restricted Forge CLI output-path preflight rejects symlinked ancestor directories, symlinked output files, and preexisting staging paths, including dangling symlinks; it also requires destination parent directories to exist. This is not a proof against adversarial concurrent filesystem races: production sandboxing must use OS-level capabilities and descriptor-relative atomic publication.

Native GCC/Clang checked-i64 primitives are additionally validated against independent compiler `__builtin_*_overflow` oracles across boundary pairs and 100,000 reproducible high-entropy signed pairs, plus near-zero divisors and extreme values. This host primitive oracle complements but does not replace actual compiled `.atto` guest testing or cross-backend evidence.

A separate executed native scalar reproducibility suite compiles 48 deterministic signed-expression `.atto` fixtures twice each to separate output filenames, hashes both emitted C++ files for byte equality, verifies source paths are absent, then compiles and runs each guest with ASan/UBSan under GCC and Clang and compares output to a separately evaluated CMake integer oracle. This is independent of full Forge JIT/AOT or actor parity.

Restricted scalar `.atto` source accepts `//` line comments and `/* ... */` non-nested block comments between tokens. Unterminated block comments fail lexically at the opening byte offset. Comments do not count against the 256-token limit but the 16 KiB source limit remains enforced. Native guest execution is tested with both kinds of comments.

The restricted native `.atto` scalar frontend accepts equivalent `int` return-signature variants: `fnc main(): int { ... }`, `fnc main() -> int { ... }`, `fnc main(): int -> { ... }`, `fnc int <- main() { ... }`, and `int <- main() { ... }`. Other return types remain outside this scalar slice. All variants compile and execute as native guests.

For restricted scalar parsing, the 24-level nesting budget measures actual parenthesis and unary nesting, not transparent calls between precedence parsing tiers. Exactly 24 nested parentheses or unary bitwise NOT operations are accepted; 25 are rejected with E_PARSE_DEPTH. Flat arithmetic chains remain limited by the independent 128-node AST budget.

For tooling and CI, `forge_scalar --check path/to/file.atto` performs bounded lexical/AST admission without creating or deleting output files; returns 0 on supported scalar source or 2 with sanitized diagnostics. This is not a full language type checker.

For the restricted integer-only scalar frontend, all expressions have the i64 result type; `infer` return annotations are therefore accepted in the same colon, arrow, block-arrow, and prefix signatures as `int` and lower to the same native i64 implementation. This is not general language-wide type inference: strings, booleans, effects, and noninteger functions remain unsupported.
