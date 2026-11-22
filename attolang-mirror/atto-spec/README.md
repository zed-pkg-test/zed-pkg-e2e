> **AI agent instructions (organization-wide):** Before working on code, issues, or pull requests, read and follow the [organization `AGENTS.md`](https://github.com/atto-lang/.github/blob/main/AGENTS.md), including its **merge-first stealth-development policy**. Honor any repository-local `AGENTS.md`, explicit holds, and GitHub-enforced rules.

# atto-spec

Canonical AttoLang language specification, including syntax, semantics, types, ownership, and concurrency.

## Canonical naming and documentation migration

[AttoLang](https://github.com/atto-lang) is the sole canonical project name; configure repository remotes as `https://github.com/atto-lang/atto-spec.git` and refer to `.atto` in new examples. [atto-spec](https://github.com/atto-lang/atto-spec) owns normative grammar, ownership, lifetime, effects and actor contracts; [atto-platform-conformance](https://github.com/atto-lang/atto-platform-conformance) tracks executed verification. Distinguish design proposals from tested compiler support and do not advertise zero-step CI as guest conformance.

## Purpose

Define normative syntax and semantics; document actor isolation, effects, ownership, and portability requirements.

## Repository responsibilities

- Keep interfaces, schemas, and behavior documented in a backend-neutral form where applicable.
- Use test fixtures and reproducible evidence to distinguish proposed behavior from implemented behavior.
- Record breaking changes, migration notes, and dependencies on other AttoLang components.

## Language contracts

- [Bitwise operators and directional streams v1](language/bitwise-and-stream-operators-v1.md): int-only `& | ^ ~`, `<<< >>> ashr`, compound updates, and typed-I/O `<< >>`, with context-sensitive caret/structural-tilde parsing; target semantics, not backend parity.

- [Trap-to-Result v1](language/trap-result-v1.md): caller-side `trap` preserves returned `Result`/`Option` layers and catches ordinary `throw` as `Err(original Error)`; unverified backends must fail closed rather than emit legacy Option results.

- [File/module `define const` v1](language/file-module-const-v1.md): unconditional static immutable bindings using `=` or `as`; constexpr folds at compile time, direct `os.read_env("KEY")` is evaluated exactly once at runtime initialization. **Proposal; backend guest execution not yet verified.**

- [Tuple destructuring, mutability and aliasing v1](language/tuple-destructuring-v1.md): accepted target syntax for mixed per-element `let`/`val`/`const`/`mut` bindings, explicit binder lists and `as foo` root aliases; compiler/guest support not yet verified.
- [Structural sequence matching v1](language/sequence-pattern-matching-v1.md): target semantics for `List.of(..._, ..., _...)`, typed `is` patterns, and explicit `as val` bindings. Its checked-in tests are **host-only**; guest compilation and cross-backend parity are **not verified**.
- [Suspension effect alias v1](language/susp-effect-shorthand-v1.md): `susp` is canonical for `suspends`; both have identical suspension effects, including on lambda and callable type syntax. Runtime and multi-backend parity require separate evidence.
- [Shared ownership and explicit guards v1](language/shared-ownership-and-guards-v1.md): TARGET semantics for `share!`/`rt share` (frozen sharing) and separately synchronized `RwLock<T>` mutation; **not compiler certified**.

- [Class field binding defaults v2](language/class-field-binding-defaults-v2.md): canonical implicit `val` for ordinary class fields, explicit `let` for writable bindings; replaces historical v1 implicit-`let` rule. Focused Rust/browser guest execution verified; cross-backend and package-linker parity remain pending.
- [Class field binding defaults v1 (historical)](language/class-field-binding-defaults-v1.md): previously adopted `let` default, retained for migration and explicit supersession tracking.

- [Opaque newtypes, bounded values and const generics v1](language/opaque-newtypes-bounds-const-generics-v1.md): TARGET nominal scalar identities, checked ranges and compile-time shape/layout arguments, with [machine-readable vectors](contracts/opaque-newtypes-bounds-const-generics-v1.json) and **host-only** proof tests. No compiler/backend guest parity claimed.

- [Implicit argument borrowing v1](language/implicit-argument-borrow-v1.md): target non-owning default for nonprimitive call arguments, checked mutable loans, explicit ambiguous cases and breaking ownership migration (not yet compiler-certified).

- [Struct field equals initializer v1](language/struct-field-equals-v1.md): restricted `StructName(field=value)` with checked name resolution, ordinary-call rejection, and source-order safety (TARGET / NOT EXECUTED).
- [Out-of-line receivers, constructors and protected class state v1](language/out-of-line-receivers-constructors-and-state-v1.md): TARGET declaration/linkage semantics for class and struct receiver methods, typed constructors, and guarded per-instance class state. Accompanying source-only cases are **not compiler/guest execution evidence**.

- [Class-field visibility v1](language/class-field-visibility-v1.md): canonical `pub[pkg]`, explicit and implicit `private`, and immutable-by-default class fields; cross-package linker tests and multi-backend guest validation remain pending.

- [Explicit callable return types v1](language/explicit-callable-return-types-v1.md): every named callable declares a concrete result (including `void`) or explicitly opts into `infer`; omissions are errors. Target contract; backend parity unverified.

- [Return-first and begin/end callable syntax v1](language/return-first-and-begin-callables-v1.md): accept `fnc int <- name(args)` and bare `int <- name(args)` plus brace, `begin`/`end`, and statement/`end` bodies (target; backend parity pending).

## Validation

Validation should rely on checked-in tests and exact-commit CI results. When reporting parity, distinguish **passed**, **failed**, **unsupported**, and **not executed**. Avoid inferring guest-runtime support from host-only tests.

## Status and development

This README records the intended role of this repository; it is not a claim of feature completeness. Consult the source tree and workflow files for actual implementation and verified build commands. Contributions should preserve compatibility with the canonical language contract.

## Ecosystem

- [AttoLang](https://github.com/atto-lang)
- [Language specification](https://github.com/atto-lang/atto-spec)
- [Cross-platform conformance](https://github.com/atto-lang/atto-platform-conformance)

## Repository ownership and contribution routing

**Belongs in this repository:** Normative AttoLang grammar, types, effects, ownership/borrowing, actors, concurrency, diagnostics and versioned language-change proposals.

**Belongs elsewhere:** Backend-specific implementations or host shims; implement in `atto-source.*`, share cross-backend fixtures with `atto-platform-conformance`.

**Safety and verification boundary:** Require explicit migration and negative cases for no implicit conversions, no operator overloading, forbidden percent/power operators, no auto-flattening of nested Option/Future, and critical/suspends admission.

This repository is the normative AttoLang (`.atto`) specification; migration-era syntax is not automatically part of current supported semantics.

Changes asserting compiler/runtime support must identify the exact source head, target, toolchain, executed positive and negative guest tests, and failure/unsupported status; an empty CI step list is **not executed**. Documentation records responsibility, **not** a claim of feature completion.
