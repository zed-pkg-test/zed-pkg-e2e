# Product spread compatibility

Status: formatter-only coverage. Formatting is **not** evidence that Java/LLVM have implemented record argument binding. See the corresponding draft `docs/FIXED_PRODUCT_ARGUMENT_SPREAD_CONTRACT.md` files on the Java and LLVM design branches.

- `foo(...(a: 5, c: "hi", b: true));` is explicit **record** expansion, with original initializer order preserved.
- `foo(...(5, true, "hi"));` is explicit **tuple** expansion.
- `foo((...));` without a spread operator remains one aggregate argument.
- Formatting must never sort record fields, remove `...`, change a named expression into a tuple, or reorder effects.
- The formatter is deliberately non-activating for still-proposed grammar and runtime semantics.
