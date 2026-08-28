# Canonical task CLI certification

This suite independently certifies the product candidate that exposes the
native schema-v2 task runtime through both:

```console
zed task ...
zed-task ...
```

The two executable surfaces must route through one implementation and produce
identical observable results for the certified subset. The test repository does
not import Rust helpers from the product and does not execute mise, asdf,
Devbox, Flox, Nix, or manager plugins.

## Exact-head contract

The workflow checks out a full 40-character `zed-pkg/zed-cli` commit, verifies
the checkout identity, builds `zed` and `zed-task`, and runs fresh-process tests
on Ubuntu, macOS, and Windows. JSON evidence records the candidate SHA,
platform, binary versions, and each case result.

A commit after the recorded candidate invalidates the evidence until the
workflow is repinned or manually dispatched with the new SHA.

## Certified behavior

- Byte-identical canonical/staged JSON and diagnostic streams for `list`,
  `info`, `graph`, and dry-run execution.
- Alias resolution and deterministic task ordering.
- Real dependency execution with scalar environment propagation.
- Argument transport through `ZED_TASK_ARGC`, `ZED_TASK_ARGS_JSON`, and
  `ZED_TASK_ARG_<n>` without argument interpolation into the task command.
- Content-verified source/output caching, exact replay skipping, and rerun after
  source drift.
- Cache records limited to schema, task name, SHA-256 identities, and output
  paths rather than environment values.
- Rejection of zero concurrency before execution.
- Rejection of live child output mixed into JSON reports.
- Confirmation required before a protected task mutates the project.
- Explicit `--yes` approval executes the same task afterward.

## Non-claims

This canary does not certify task-local tool installation, secret providers,
template evaluation, sandboxing, network policy, process-tree cancellation,
watch mode, or manager-native task parsing. Those remain explicit product and
certification slices.

Runtime execution itself requires no network and uses only temporary project,
home, cache, and evidence paths. Cargo may access its normal dependency sources
while building the reviewed candidate; that build activity is separate from the
runtime claim.

## Evidence schema

Each platform uploads one JSON artifact using:

```text
zed-pkg-test/canonical-task-canary/v1
```

The report contains no environment values or credentials. Failure details are
bounded to command diagnostics and assertion context generated inside the
temporary fixture.
