# Compiler parity acceptance gate

This harness tests whether a reviewed `oresfmt` binary changes **source admission** in the Java/GraalVM and LLVM compilers. It does not prove runtime equivalence, ownership soundness, or backend parity for successful programs.

## Invocation

```sh
python3 scripts/check_compiler_parity.py \
  --formatter './target/release/oresfmt' \
  --java-command '/path/to/java-source-compiler --check {source}' \
  --llvm-command '/path/to/llvm-source-compiler --check {source}' \
  --cases tests/compiler_parity_cases \
  --timeout 30 \
  --max-case-bytes 1048576
```

Use real, pinned executable compiler builds, not the mock programs from `scripts/test_compiler_parity.py`. The `{source}` placeholder is mandatory in each compiler command. The harness passes arguments without a shell and provides no interactive stdin. Commands must return **0 for accept, 1 for an ordinary source rejection, 2 for usage error**. Signal termination and other nonzero codes are failures, not valid syntax rejection.

For each `*.ores` fixture, the harness snapshots and validates a regular, nonsymlink UTF-8 source file, runs `oresfmt --stdout`, rejects erased/oversized/non-UTF-8 formatted text, checks formatter byte-level idempotence, and compares compiler admission **before and after** formatting on both backends. Each process has a timeout. Compiler stdout is discarded; diagnostic stderr is retained only up to 4,000 bytes. Formatter stdout is accumulated with a byte bound of twice `--max-case-bytes`; it is **not** an unlimited in-memory capture or a promise that the output is streamed directly to disk.

The command returns exit code 0 only if every fixture has unchanged valid admission, formatting reaches a byte-identical fixed point, at least one fixture is accepted by **both** compilers, and Java/LLVM admission agrees for every fixture. A compiler rejecting both forms is `UNSUPPORTED` rather than evidence of semantic parity, even when it is an unchanged rejection.

## Interpreting JSON results

- `ok`: aggregate gate result; a false value blocks promotion.
- `shared_accepted`: number of fixtures accepted by both backends; the normal gate requires at least one.
- `results[].backends.{java,llvm}.{original,formatted}`: `accept`, `reject`, `usage`, `unavailable`, `timeout`, `crashed`, or `error`. Only matching `accept`/`reject` values can pass individual fixtures.
- `results[].backend_agreement`: both backends gave the same classifications before and after formatting.
- `results[].idempotent`: formatter pass 2 emitted identical bytes to pass 1.

The diagnostic switches `--allow-zero-shared-accepted` and `--allow-backend-divergence` relax only the respective **aggregate** gates. Never enable these switches as the merge or release gate. Any `results[].ok=false` still fails.

## Threat and execution boundaries

The fixture byte limit protects against unexpectedly large input and formatter expansion. Output capture and stderr retention are bounded. `--max-case-bytes` is operator-controlled but explicitly capped at 8 MiB (16 MiB maximum formatter stdout). Oversized limits fail before executing any child process. Child processes are terminated on timeouts/output floods. This is **not** a sandbox: run only reviewed compilers and formatter binaries in an isolated CI runner with appropriate privileges and filesystem/network restrictions.

Before declaring a passing compiler-parity gate, pin the exact formatter binary and both compiler commits, verify the source Git trees, and run real accepted, rejected, malformed and ownership-sensitive fixtures. Save the JSON result alongside the input SHA manifest. Separately run Java/GraalVM and LLVM guest execution, native/AOT and language-conformance tests; source admission alone cannot establish those properties.

## Offline self-tests

```sh
python3 -m unittest discover -s scripts -p 'test_compiler_parity.py' -v
```

These mocks test harness behavior only; passing them is **not** compiler correctness evidence.
