# atto-source.java

AttoLang compiler and actor runtime targeting the JVM, GraalVM, and GraalVM Native Image AOT.

## Naming and specification migration

This is the canonical [AttoLang backend repository](https://github.com/atto-lang/atto-source.java). Use `.atto` for portable AttoLang source and update local remotes to `https://github.com/atto-lang/atto-source.java.git`; do not use historical repository links or naming in new examples.

- **Normative grammar and semantics:** [atto-spec](https://github.com/atto-lang/atto-spec). Check the current versioned contracts for ownership operations (`borrow!`, `take!`, `share!`, `copy!`, `copy!!`), implicit argument borrowing, `lt!` binders, `eq`/`neq` value equality, `susp` effects and file/module `define const`. These are *specification targets*, not claims of executable support in this backend.
- **Positive and negative guest execution evidence:** [atto-platform-conformance](https://github.com/atto-lang/atto-platform-conformance) and [atto-compilers-parity-testing](https://github.com/atto-lang/atto-compilers-parity-testing). Report `passed`, `failed`, `unsupported` or `not executed`, and pin exact compiler, source tree, target and toolchain.
- **Portable shared sources and target constraints:** [atto-common-source](https://github.com/atto-lang/atto-common-source) and [atto-target-profiles](https://github.com/atto-lang/atto-target-profiles). Keep backend-specific gaps explicit, and do not infer guest parity from host tests or zero-step CI runs.

## Scope

This repository is intended to host the Java/GraalVM compiler backend, actor runtime integration, and supporting build and conformance tooling.

## Status

Repository initialization and implementation status should be checked against the source tree and CI. This README describes the intended scope and does not assert that every feature is implemented.

## Ecosystem

- [AttoLang language organization](https://github.com/atto-lang)
- [Atto commercial infrastructure](https://github.com/atto-co)

## Development

See the repository's source tree, workflows, and contribution documentation for verified build and test instructions. Do not assume cross-backend parity without executed conformance results.

## Repository ownership and contribution routing

**Belongs here:** Java/GraalVM compiler, JVM execution, Graal Native Image AOT path, Java host-library import admission, actor-owned heap/stack runtime and guest execution tests.

**Belongs elsewhere:** Normative semantics live in atto-spec; portable .atto library source belongs in atto-common-source; LLVM/.NET/Rust/Wasm implementation must remain in their own backend repos.

**Security and portability boundary:** Host Java/Graal library calls require explicit capabilities, ownership and lifetime fencing. Native Image compilation or Epsilon host smoke alone is not proof of zero GC, isolation or guest-level parity.

AttoLang is the canonical project identity. Use `.atto` source, `atto-*` package and repository names, and the active AttoLang CLI contract for current documentation. Do not duplicate normative semantics from [atto-spec](https://github.com/atto-lang/atto-spec).

**Evidence gate:** record exact compiler/runtime head and tree, target/toolchain, nonzero executed steps, positive and negative guest results, and explicit passed/failed/unsupported/not-executed states. Host-only CI and `steps=[]` do not establish guest support. README scope is not a feature-completeness claim.
