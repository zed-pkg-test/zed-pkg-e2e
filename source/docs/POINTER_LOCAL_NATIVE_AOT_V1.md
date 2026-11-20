# Forge: bounded local Pointer<int> native compiler candidate

This implementation targets [atto-spec #18](https://github.com/atto-lang/atto-spec/pull/18)
and the canonical PRL-001/002 cases in
[atto-platform-conformance #30](https://github.com/atto-lang/atto-platform-conformance/pull/30).

## Compiler semantics

The actual Forge scalar lexer, parser and native C++ code emitter support
exactly one top-level synchronous `pub fnc main(): int` with one initialized
`let value: int = 42;`, a nonowning `val Pointer<int> ptr = rt ref value;`
(or `ref!value`), one read (`rt deref ptr` / `deref!ptr`), exactly one consuming
`rt unref ptr` / `unref!ptr`, and a scalar `return answer;`. All operations
are lexically checked using source offsets, including adjacency of bang suffixes.
Untrusted Atto identifiers never enter emitted C++ identifiers.

A separate typed `BorrowedLocal` admission plan is verified as one
read-copy, distinct non-shadowing source/handle/result names, and precisely
one release before the only return. The actual generated C++ guest uses a
native immutable stack place, a compiler-created const pointer to that place,
and a primitive read. It nullifies the pointer at unref, never deletes the
underlying stack local. This is NOT an owning `Pointer.alloc<T>`, general
`Pointer<T>` lowering, unsafe address, FFI, heap allocator, actor messaging,
TenantVM sharing, async continuation, exception cleanup or alias mutation.

The stage-0 Forge scalar guest ABI prints the logical result (e.g. "42\n")
and exits 0. Other compilers expose the logical main result directly. The
stage-0 output convention is a remaining process-ABI parity gap.

## Canonical fixtures and tests

- `fixtures/pointer-local-v1/PRL-001.atto` is byte-identical to
  canonical Git blob `6056400afa8d7b9b71f29be5ca4cfb15240a59b3`
- `fixtures/pointer-local-v1/PRL-002.atto` is byte-identical to
  canonical Git blob `8fa66e68b386c2c6d1f2b15b9399e70a116c0828`
- `tests/pointer_local_native_guests` uses the real Forge compiler to compile
  4 source guests; C++20 GCC/Clang builds and executes the binaries under
  ASan/UBSan when the existing sanitizer option is enabled, checks their
  printed logical result, and compares emitted C++ SHA-256 for canonical
  rt-vs-bang aliases
- `tests/pointer_local_typed.cpp` mutates typed IR to ensure forged local
  handles, values, provenance and detached AST nodes cannot be emitted
- 24 negative *.atto fixtures must reject before producing an output .cpp
  file; two unsupported member-call spellings fail the canonical lexer and
  the others fail the strict pointer-profile parser

To run the complete existing CTest suite and these new tests:

```sh
cmake -S . -B build -DCMAKE_CXX_COMPILER=clang++ -DATTO_FORGE_SANITIZERS=ON
cmake --build build --parallel 2
ctest --test-dir build --output-on-failure
```

Repeat with a separate GCC build. The native code and tests have been
COMMITTED but are NOT_EXECUTED on an allocated runner; only a genuine
exact-head compiled guest execution may certify parity. Do not mark any
of the 36-case or 11-backend conformance slots passed merely from
authored fixtures, source hashes or zero-step Actions failures.

## Overlap and review gates

Open Forge PRs [#44](https://github.com/atto-lang/atto-source.forge/pull/44),
[#42](https://github.com/atto-lang/atto-source.forge/pull/42),
[#49](https://github.com/atto-lang/atto-source.forge/pull/49) and
[#36](https://github.com/atto-lang/atto-source.forge/pull/36) also change the
scalar lexer or parser. Integrate their independent channel, sequence,
pipe, map and record features deliberately; no blind cherry-pick. PR #45
also touches CMake but has separate functional-pipeline semantics. The legacy
[Oreslang Forge #120](https://github.com/ores-truffle-oreslang/oreslang-source.forge/pull/120)
is not proof of compatible Pointer<int> ownership.

Related candidate compilers: [Rust/Wasm #419](https://github.com/atto-lang/atto-source.rs/pull/419),
[LLVM #64](https://github.com/atto-lang/atto-source.llvm/pull/64),
[.NET #22](https://github.com/atto-lang/atto-source.net/pull/22).
All remain unverified pending actual guest execution.

## Audit: preserve caller-owned output on rejection

An invalid Atto source previously caused `forge_scalar` to delete an existing caller-supplied `.cpp` output file. Rejection is never authority to remove or modify caller-owned data. The CLI now preserves existing output on source-length, read, lexical, syntax and AST rejection. Only a compiler-created incomplete `.part` file may be cleaned up.

The native pointer test suite now checks all 24 invalid fixtures twice: a fresh output must remain absent, and a pre-existing sentinel must survive byte-identically. The scalar guest and CLI diagnostic suites also assert preservation. The old binary fails the new regression; the patched compiler passes under GCC and Clang with ASan/UBSan. This does not prove filesystem race safety: symlink-safe directory-descriptor-relative output publication is still required to close malicious concurrent-path-replacement races.
