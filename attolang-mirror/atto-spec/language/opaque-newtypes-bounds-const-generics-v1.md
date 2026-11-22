# Opaque newtypes, bounded values and const generics v1

**Status: TARGET / NOT EXECUTED.** This is a new AttoLang language contract, not a claim that any compiler accepts or executes these examples. The initial implementable slice is exact integer newtypes, interval constraints and nonnegative compile-time integer dimensions. It extends [compile-time constants](compile-time-constants-v1.md) and [ownership vocabulary](ownership-vocabulary-v1.md).

## Motivation and canonical examples

~~~atto
define newtype UserId(i64);
define newtype OrderId(i64);
define newtype Port(i64)
  where value gte 1 and value lte 65535;
end

fnc make_port(i64 raw) -> Result<Port, RangeError> {
  return Port.try_from(raw);
}

fnc send_to(Port port) -> void {
  // Reading a Port does not create an i64-typed argument.
}

define struct Matrix<T, const usize R, const usize C> as
  data: Array<T, R * C>;
end

fnc identity(Matrix<f32, 4, 4> matrix) -> Matrix<f32, 4, 4> {
  return matrix;
}
~~~

These are representative **target** programs. Existing preflight parsers or backends MUST NOT treat accepting this syntax as proof of executable semantics. Portable v1 requires an explicit supported numeric base; wider, unsigned and floating bases may remain unsupported until each backend proves its representation and validator. The shown Matrix layout also requires fixed-size Array support on the target.

## Declaration, scope, identity and visibility

* Canonical spelling is `define newtype Name(Base);` or `define newtype Name(Base) where predicate; end`. Public visibility uses the existing `define pub newtype` convention. `newtype` itself grants opacity; no additional `opaque` keyword is required in v1.
* A newtype is **nominal**, not a transparent alias. The identity key is (canonical package/module origin, declared symbol), never its underlying representation, unqualified source spelling, or hash alone. Two newtypes of i64, including identical bounds, are distinct. Imported references must resolve to their defining identity, not re-create it.
* Newtypes are sealed as single-representation wrappers, not subclassable classes, structural records, or enum cases. Their representation is private outside the defining module. Inside the defining module, representation access may be privileged but may not bypass a declared invariant when creating or mutating a value.
* `type Alias = i64` remains a transparent alias. A newtype cannot be implicitly converted to or from its base, another newtype, or a transparent alias. Overload resolution, pattern matching and generic specialization use nominal identity. No implicit numeral-to-newtype promotion. A generic `T` is not the base type merely because its layout is compatible.
* V1 newtypes have one fixed-size, statically laid-out base or a separately proven immutable scalar handle. Reference-dependent, recursive, unsized, dynamically allocated, generic-unknown and resource-owning representations require further contracts. Newtype copying, borrowing, taking and sharing obey the **base's** ownership traits, not assumed Copy semantics.

## Checked constructors and invariants

* `Name.try_from(base_value)` yields `Result<Name, RangeError>` (or a more specific declared validation Error). An unchecked successful constructor is not publicly available. `Name.from_const(literal)` is admissible only after full compile-time proof; otherwise fails compilation.
* Validate using the exact typed integer domain, not binary64 coercion; intervals are closed and use `gte/lte` (the `>=/<=` aliases are equivalent). Nonempty intervals with optional finite exclusions are normalized at declaration time. Reject an empty domain, out-of-base representable bounds, malformed values and unproved arbitrary `where` predicates. Do not confuse Unicode scalar length with byte length in later string refinements.
* Constant-known successful values may be statically constructed and require **zero runtime validation**. Runtime-unknown values require a checked constructor and a real failure edge; if a backend cannot lower the check, it must fail compilation.
* Checked arithmetic on bounded values is never assumed closed under the range. The result is a base-type scalar unless an operator is explicitly and independently proved to preserve the newtype invariant. Overflow, wraparound, narrowing and range holes must fail closed; integer min/max evaluation is exact.
* When a parameter is a `Port`, its checked invariant is already established. An explicit `as` conversion is **not** an unsafe constructor or a way to defeat nominal identity. Unwrapping to the base is an explicit, visibility-controlled operation; an unsafe FFI/deserialize path is not a `try_from` substitute.
* Flow-sensitive refinement is valid only if an immutable proof witness dominates every use and remains true after mutations, exceptions, async suspension, actor publication and aliasing. V1 does not infer unproven arbitrary predicates or silently insert validations into previously unchecked expressions.

## Const value generics and layout

* Value-generic parameters are declared `const usize N` and used alongside type parameters, e.g. `Matrix<T, const usize R, const usize C>`. `usize` is a nonnegative compile-time integer in the target's admitted range; it is not a runtime argument. Numeric dimension actuals are compile-time expressions evaluated with checked integer arithmetic.
* Generic specialization keys contain both nominal type IDs and the **evaluated typed const values**. `Matrix<f32,4,4>` and `Matrix<f32,2,8>` are not assignable or layout-equivalent at the type level even when byte counts match. Const parameter names and shadowed lexical bindings cannot alter identity.
* Dimensions may appear in fixed-array lengths and layout expressions, such as `Array<T,R*C>`. Multiplication and byte-size multiplication must use checked target-width arithmetic, with positive/zero-size rules enforced by the containing type. V1 permits literals, other const parameters, and pure typed arithmetic which the compiler can fully evaluate. Unresolved symbolic equality (e.g. `R+1 == S` for arbitrary R,S), runtime values, side effects, environment reads and unbounded recursive constexpr are rejected until a separate solver/const-eval contract exists.
* A backend must reject overflowing or resource-exhausting dimensions **before** allocation or codegen, using deterministic bounded evaluation/monomorphization budgets and a target-profile maximum layout size. No silent truncation, wrapping, VLA fallback, dynamically sized heap replacement, or failed-zero-size exemption.
* A `Matrix` indexed with a provably in-bounds compile-time index can omit runtime checks; unknown indices need real bounds checking. Fixed arrays are inline where the target profile promises inline storage; shape alone does not guarantee stack placement.

## Representation and boundaries

For an eligible newtype, `size_of<Name>() == size_of<Base>()` and `align_of<Name>() == align_of<Base>()` in the same target profile, with no per-instance tag, heap box, GC header or dynamic check **after** a successful construction. This is a **layout goal with a mandatory backend ABI proof**, not authorization to erase nominal type identity in typed IR, optimizer checks, debug diagnostics, serialization schemas, actor-message contracts or hot-reload descriptors.

Cross-module, FFI, actor and serialization admission must preserve (or independently validate) the fully qualified nominal type and invariant. Deserialization validates before publication; direct raw memory reinterpretation is not a trusted constructor. ABI exposure must be explicitly declared, and compatibility must be versioned across recompiled modules and hot reload. Runtime provenance or capability checks may exist at boundaries without adding per-value storage overhead to local scalar newtypes.

Const generics likewise cannot hide layout differences at ABI boundaries: the complete evaluated argument list participates in type identity, serialization shape and binary layout fingerprint. Never materialize a value of a different shape by byte reinterpretation.

## Required diagnostics and negative cases

| Code | Example of prohibited operation |
| --- | --- |
| `E_NEWTYPE_MISMATCH` | Passing `OrderId` where `UserId` is required |
| `E_NEWTYPE_CONSTRUCTION` | Raw assignment/cast/deserialize used as unchecked nominal construction |
| `E_RANGE_INVALID` | Out-of-bounds constant or contradictory/empty range |
| `E_RANGE_UNPROVEN` | Arbitrary predicate or unsafe narrowing without a proof/check |
| `E_CONST_GENERIC_ARG` | Runtime value, negative dimension or unsupported const expression |
| `E_CONST_OVERFLOW` | Integer operation or layout product overflows |
| `E_CONST_LAYOUT_BUDGET` | Finite evaluation or target storage budget exceeded |
| `E_CONST_SHAPE_MISMATCH` | Passing `Matrix<T,2,8>` for `Matrix<T,4,4>` |
| `E_NEWTYPE_ABI` | FFI, actor, hot reload or wire protocol loses nominal identity |

These diagnostic names are target stable families; exact compiler emissions need backend evidence. Error messages must never leak an uninitialized value, fabricate a successful `Result`, or emit a runnable guest when the type rules reject the program.

## Relationship to prior work

Java [atto-source.java PR #792](https://github.com/atto-lang/atto-source.java/pull/792) stages refinement/newtype parsing, bounded interval and nominal proof logic, with an intentional fail-closed TypeChecker gate. [atto-source.java PR #1092](https://github.com/atto-lang/atto-source.java/pull/1092) hardens descriptor identities and interval metrics. Integrate those mechanisms and resolve overlaps rather than copying an unsafe runtime into another compiler. The earlier refinement work does **not** establish executable checked construction, const-generic layout or native parity. All language backends must be treated as **NOT VERIFIED** for this combined v1 contract until independently executed.

Rust's value const-generics and Scala's opaque aliases inform the design, but AttoLang keeps its own `const` meaning, `define newtype` grammar, `try_from` discipline, `take!`/`borrow!` ownership rules, and actor isolation model.

## Required implementation sequence and evidence

1. Shared canonical grammar, type/const AST, nominal identity and integer interval normalization (the shared spec/conformance target).
2. Compiler frontend: fail-closed static mismatch diagnostics, checked construction, const-evaluator and layout arithmetic; preserve nominal shape metadata through typed IR.
3. Real guest execution: positive range boundaries and matrix storage/indexing, negative compile diagnostics, runtime-unknown invalid input, and cross-module/actor/FFI/serialization spoofing tests.
4. Exact-head parity receipts per Java, LLVM, .NET, Wasm, Forge, Rust, JS, Cranelift, HDL and other target profiles. Each must name source commit + tree/blob SHAs, executed compiler and compiled guest, environment, accepted/rejected cases and performance/layout verification. A parser-only pass, host model, smoke fixture or zero-step CI is **NOT EXECUTED** for guest parity.

See the companion machine-readable contract `contracts/opaque-newtypes-bounds-const-generics-v1.json` and shared fixtures in `atto-platform-conformance`. The initial test harness is host-only and is not a substitute for executing the AttoLang compiler.
