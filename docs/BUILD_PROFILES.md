# Oreslang build profiles v1

This document defines the backend-neutral build-profile contract used by the Oreslang CLIs and compilers.

The normative machine-readable copy is `build-profiles/v1.json`. A backend may support only a subset of these profiles, but it must **fail closed** rather than silently weakening a requested profile.

## Common rule: no runtime reflection

Runtime reflection is forbidden in every v1 build profile. Oreslang should use compile-time introspection, generated serializers/deserializers, explicit registries, explicit FFI/export roots, and closed-world dispatch metadata instead.

Compile-time inspection is not runtime reflection. Metadata generated at build time is allowed when it has an explicit consumer and can itself be tree-shaken.

## Profiles

| Profile | Required behavior |
| --- | --- |
| `dev` | Development build. Runtime reflection remains forbidden. JIT/dynamic code may be available on backends that support them. Tree shaking and metadata/runtime pruning are optional. |
| `release` | Requires semantic tree shaking, runtime-feature pruning, metadata pruning, and backend DCE/LTO where supported. Runtime reflection remains forbidden. |
| `strict-aot` | Inherits `release`. Requires a closed-world full-AOT artifact. JIT and unresolved dynamic code loading are forbidden. All external roots must be explicit or compiler-generated. |
| `hard-rt` | Inherits `strict-aot`. Also forbids GC, unbounded allocation and unbounded waits; requires cooperative/bounded code and WCET/deadline admission evidence. This profile is an admission contract, not a safety certification by itself. |

The convenience CLI spellings are `--release`, `--strict-aot`, and `--hard-rt`. They are mutually exclusive selectors; inheritance is semantic, so `--hard-rt` already includes the requirements of `strict-aot` and `release`.

## Freestanding

`--freestanding` is an orthogonal constraint and may be combined with any build profile. It forbids hosted stdlib roots, OS/libc dependencies, and implicit platform providers. Any target provider must be selected explicitly.

## Required backend behavior

A backend must never claim a stronger profile than it can prove. Until a backend has the necessary admission pass, it must reject that profile with a diagnostic explaining the missing gate.

Initial integration status:

- Java/GraalVM: `release` and `strict-aot` can reuse existing tree shaking and AOT execution-mode checks. `hard-rt` must remain fail-closed until the active realtime-admission work is wired into the launcher/build path.
- LLVM: full-AOT is the natural code-generation model. `hard-rt` must additionally pass bounded realtime admission; a target merely producing LLVM IR is not sufficient evidence.
- Rust/Cranelift: the current scalar compiler is already reflection-free and native-AOT. The first hard-RT gate should reject recursive/unbounded call graphs and remain intentionally narrower than the full language.

## Tree shaking and explicit roots

Release-or-stronger builds retain only the semantic closure of explicit roots. Core, alloc, actor runtime, stdlib, user code, generic instantiations, error paths, metadata, dispatch tables, async continuations, and runtime subsystems are all removable when unreachable.

Reflection must not be used as a reason to conservatively retain arbitrary code. External reachability is expressed through explicit FFI/export roots, generated registries, reload contracts, or future versioned reachability manifests.
