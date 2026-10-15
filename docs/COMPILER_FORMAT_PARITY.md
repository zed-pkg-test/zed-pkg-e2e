# Compiler-backed formatter acceptance parity (experimental)

The formatter is not a semantic compiler. It cannot prove that two programs
have identical runtime behavior. This harness offers a **narrower, honest gate**:
the original and formatted source must retain the same acceptance status in
each independently provided compiler; the two compilers must agree on
admission for shared-subset fixtures; and formatting must be idempotent.

The fixtures in `tests/compiler_parity_cases` are deliberately restricted to
the small scalar intersection of the current Java/GraalVM and LLVM frontends,
based on LLVM's `constant-main.ores` and `forward-call-main.ores` fixtures.
They are not evidence of actor, networking, streams, channels, or import parity.

## Run against real compiler executables

Build the reviewed formatter and both compilers independently. Use their real
command-line invocations (with parse/compile-only options as applicable):

```sh
cargo build --bins
python3 scripts/check_compiler_parity.py \
  --formatter './target/debug/oresfmt' \
  --java-command 'JAVA_COMPILER_COMMAND {source}' \
  --llvm-command '/path/to/oreslang-llvmc {source}'
```

Use the real Java launcher `dev.oreslang.launcher.OresMain --check`
with its built classpath (or a reviewed packaged equivalent). Its `--check`
mode invokes `LinkedProgramRunner.validate` without running guest `main()`.
For example, after building the Java repository with its GraalVM dependencies:

```sh
  --java-command 'java -cp /path/to/java/classes:/path/to/dependencies/* dev.oreslang.launcher.OresMain --check {source}' \\
  --llvm-command '/path/to/oreslang-llvmc {source}'
```

The example lines above are command *arguments* for the Python harness,
not a separate shell command. Set the Java classpath and native compiler
binary paths explicitly. The LLVM compiler emits LLVM IR on success; the
harness captures that output without executing a guest program.

The Java `--check` command performs linked validation (including semantic
and import checks), while the LLVM command also performs supported-subset
lowering to IR. Consequently, this is **compiler admission parity**, not a
pure grammar-parser or normalized-AST comparison.

Exit code 0 means accepted, 1 means ordinary compiler rejection, 2 is a
broken compiler invocation, negative status means a signal/crash, and any
other exit status is treated as a tool failure. Timeouts or unavailable
compiler binaries also fail closed.

The harness creates fresh temporary source files, does **not** execute
generated guest binaries, never invokes a shell, and records only compiler
accept/reject/timeout/unavailable status. It fails if the formatter is not
idempotent, if either compiler changes its acceptance decision after
formatting, or if either backend times out or cannot run. By default the command also **requires at least one fixture to be accepted by
both compilers**, and requires both compilers to agree on each fixture's
admission status. Incompatible backends are not silently treated as passing.
For non-gating diagnostic inventories only, `--allow-zero-shared-accepted`
and `--allow-backend-divergence` explicitly relax these checks.

An individual backend rejecting both forms is reported as unsupported, not
as proof of semantic equivalence. Compiler acceptance parity alone is **not**
a runtime, AST-equivalence, ownership, or generated-code equivalence proof.

## CI separation

`python3 -m unittest discover -s scripts -p 'test_compiler_parity.py'`
tests harness behavior with deliberately simulated compiler commands. This
is a **harness self-test only**. Actual Java/LLVM cross-backend admission
requires independently built compiler binaries and the command above; those
are not silently substituted or fetched across private organizations.

All fixtures should eventually be annotated with explicit backend
capability tiers once Java and LLVM offer stable parse-only entrypoints.
The proposed next gate is to compare compiler ASTs under a normalized
representation, not just exit codes, and then add runtime differential
checks for their common executable subset.
