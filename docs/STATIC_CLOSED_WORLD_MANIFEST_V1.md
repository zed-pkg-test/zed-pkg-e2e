# Restricted Graal Native Image compiler-generated closed-world actor graph

## Status and provenance

Experimental compiler-emitted v1 *pilot* evidence for roadmap task #31. Stacked on AttoLang Graal compiled guard PR #7 and original static actor compiler migration PR #1. This does NOT claim a production Java/Graal Truffle language frontend, universal full-AOT sealed graph, or five-backend portable stdlib/typeclass support.

The production restricted static compiler `tools/atto_static_actor_aot.py` first admits one exact SHA-256-pinned `.atto` source file, parses bounded actor declarations, and statically emits a Java Native Image program. On this branch the compiler additionally builds the `closed_world` record in `manifest.json` from the *same parsed types and actions*. The record is not derived from a source regex or independently supplied user JSON.

## Scope and definitions

- `declared_actor_nodes`: every parsed actor declaration, with its generated final Java type name.
- `actor_roots`: only actor types targeted by a checked `spawn` action, with their generated send and retirement methods; a type may be declared but unused.
- `actor_instance_spawns`: number of actual spawn actions, distinct from number of rooted types. The source compiler validates actor handle admission and retirement along the statically admitted path.
- `entry_roots`: the one generated `guest_main` entry. `action_count` / sorted `action_kinds` enumerate the source-owned program actions (spawn/send/stop/guard).
- `host_primitives`: approved Java checked arithmetic, FFM confined off-heap actor arena, GC metric instrumentation, and standard-output observation; these are trusted host services, NOT dynamically imported guest functions.
- `generic_specializations`, `typeclass_witnesses`, `guest_dynamic_imports` are empty because the source parser REJECTS those forms, not because the full language has implemented or closed their call graphs.
- `guest_dynamic_loading`, `guest_runtime_reflection`, `guest_runtime_codegen` are false for this admitted guest subset; known dynamic Java APIs are rejected by generated code scan, although a final linked Native Image call graph is NOT analyzed.

## Proof boundaries

The `status` is intentionally `COMPILER_GENERATED_RESTRICTED_GRAPH_NOT_CERTIFIED` and the flags `native_aot_artifact_verified=false`, `authenticated_execution_receipt=false` remain false after compilation. Actual Native Image machine-code execution is a separate source-owned GitHub Actions step. Neither passing metadata tests nor a short `--gc=epsilon` native run certifies zero lifetime tracing GC, tenant sandbox isolation, or the complete target-profiles `atto.aot.closed-world.v1` model. The record does NOT claim to enumerate dynamically reached Java runtime libraries, JVM Native Image internals, OS calls, caller-approved external assemblies, actor/task scheduler callbacks, or other compiler backend roots.

Security invariants: no dynamic source execution; no runtime proxy/reflection API as guest code; bounded source and actors; all actor-owned arenas must close before a guard early-exit; no `.atto` source is exported outside this repository. Compilers in other AttoLang backends must independently produce their own authenticated roots and import graph before cross-backend parity is claimed.

## Reproduce

`python3 -m unittest discover -s tests -p test_static_actor_aot.py -v` checks explicit and reachable nodes, static guard path, manifest determinism, denial of unknown dynamic API syntax, and absence of positive runtime certification. Source-owned Graal Actions pipelines also build and execute the actual static guest Native Image program (baseline return 43 and guard return 7) with exact-source commit pinning.

Related proposals: [AttoSpec guard and typeclass #4](https://github.com/atto-lang/atto-spec/pull/4), [Atto Target Profiles structural AOT receipt #1](https://github.com/atto-lang/atto-target-profiles/pull/1), [Atto canonical stdlib #6](https://github.com/atto-lang/atto-common-source/pull/6).
