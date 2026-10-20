# Freestanding library-tier admission (issue #21)

The **compiler** must distinguish freestanding `core` from opt-in `alloc`,
OS-independent actor primitives, and hosted `stdlib`. This repository's
existing resolved-closure validator is an **effect-admission checker**, not a
source compiler, linker, allocator, WIT parser, or evidence of a bare-metal ELF.

| Tier | Dependencies | Required conditions |
| --- | --- | --- |
| `core` | `core` only | No host capabilities, allocator, OS, clocks, threads, FFI, Wasm, WIT or reflection |
| `alloc` | `core`, `alloc` | Explicit allocator availability and heap effect granted at target and build |
| `actor` | `core`, OS-independent actor primitives | No implied heap, threads or clock; hosted adapters opt in separately |
| `stdlib` | Admitted lower tiers | Specific inferred I/O/OS effects and target grants; never part of mandatory `core` |
| `zorde` | Independent admitted validation contract | Static schema validation may be freestanding; TypeSpec and WIT tooling is **build time only** |

`profiles/embedded.json` describes the *upper bound* of a potentially
hosted embedded environment. It is **not** a freestanding-core promise:
some embedded hosts may offer a heap, clock, GPIO, or filesystem. A truly
freestanding build must pass a resolved closure with no grants, and a
post-link negative-symbol/import check. The latter gate is not implemented
in this repository.

`tests/freestanding-boundary.test.mjs` adds negative admission probes for
a pure `core` dependency on `alloc` or hosted effects. The tests require
the graph to declare the compiler-inferred effects truthfully; a dishonest
compiler graph is not caught by a JSON metadata check.

## Outstanding hard gates

1. Introduce a versioned `libraryTier` field in a separately reviewed
   compiler-emitted manifest; reject forbidden tier-to-tier imports *before*
   accepting a resolved closure. Do not silently assign tiers from names.
2. Independently authored TypeSpec and JSON Schema must agree under
   pinned `tjsv` with deliberately failing mutation fixtures.
3. Define WIT Component Model version/world/import/export/resource/borrow
   constraints for hosted Wasm **without** adding WIT requirements to bare metal.
4. Pin the exact LLVM source, compile and link a freestanding program and
   reject forbidden OS/libc/allocator symbols and imports.
5. Emit exact source tree, compiler binary hash, linker/target, dependency
   closure and negative proof receipts before any release certification.

These checks do **not** certify a working freestanding compiler or native
runtime. The named tiers above are a proposed enforcement matrix, not yet a
compiler-checked feature.
