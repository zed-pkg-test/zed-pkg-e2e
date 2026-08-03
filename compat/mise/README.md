# mise compatibility certification

This directory independently certifies the compatibility claims made by
`zed-pkg`. It does not grant feature credit for command names or for preserving
configuration bytes alone: each tier has observable acceptance criteria and
negative canaries.

## Tier 1: lossless project adapter

The initial suite covers project-local mise configuration interoperability:

- `.mise.toml` and `mise.toml` discovery;
- deterministic import to `zed-dev.toml` plus `.zed/mise.lock` provenance;
- frozen verification before and after a lossless export;
- multi-version order preservation;
- typed `env` and `vars` plus task/settings preservation;
- unknown top-level hooks/plugins preserved but never executed;
- conflicting project config rejection;
- `.tool-versions` reported as unsupported until its dedicated adapter lands;
- source and generated-plan tamper rejection.

The machine-readable claim surface is `tier1-cases.json`. A release must not
claim a higher tier than the latest passing certification report.

## Running locally

Build `zed-cli`, then run:

```sh
python3 scripts/mise_tier1_certify.py \
  --zed /path/to/zed-cli/target/debug/zed \
  --fixtures compat/mise/fixtures \
  --report mise-tier1-report.json
```

The runner copies every fixture into a temporary directory. It never executes
commands from imported mise hooks, plugins, tasks, or templates.

## Compatibility vocabulary

- **supported**: Zed interprets the field and the suite checks semantics.
- **preserved-inert**: Zed round-trips the field losslessly but does not execute
  it without a separately certified native implementation and trust policy.
- **unsupported-explicit**: Zed rejects or diagnoses the input; it is never
  silently discarded.
- **negative-canary**: malformed, mutable, conflicting, or tampered state that
  must fail closed.
