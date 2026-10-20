# Experimental core Wasm i64 artifact manifest v1

This is a pinned zero-import, zero-capability, scalar-only guest contract. Required fields: schemaVersion 1, artifactType oreslang.core-wasm.i64-v1, target wasm32, backend wasm, environment browser/edge-js/edge-wasi, entry, exact SHA-256 and byte count, empty imports/capabilities, max memory pages and compiler/source/IR SHA-256 provenance.

A manifest is untrusted input and cannot authorize host capabilities. The browser must check the bytes and actual import/export declarations before instantiation. These digests prove byte identity but do not attest that a source commit produced the compiler binary. Source-level effects are NOT inferred, and LLVM still rejects target headers. This format is experimental, not evidence of feature parity.
