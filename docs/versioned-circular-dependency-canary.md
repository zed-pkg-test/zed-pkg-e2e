# DEN-3488 exact-version circular dependency canary

This acceptance canary checks the public `zed` executable against the exact
multiversion graph requested by DEN-3488:

```text
A@1 -> B@1 -> A@2 -> B@0 -> A@2
```

The CLI source is pinned to one immutable `zed-pkg/zed-cli` commit in
`pins/den-3488.json`. The workflow refuses a non-40-character pin and verifies
the checkout SHA before building. It never reads a token or writes to a remote
registry.

## Black-box assertions

`scripts/versioned-circular-dependency.sh` creates four independent payload
sources and invokes:

```text
zed graph materialize --plan <plan.json> --project <project>
```

The canary verifies that:

- `A@1`, `A@2`, `B@1`, and `B@0` remain four distinct exact graph nodes;
- the terminal diagnostic names `A@2`, `B@0`, and the closing back-edge;
- only four canonical node directories are created;
- each dependency edge is a symlink to an existing canonical node;
- `B@0 -> A@2` closes the cycle by pointing to the first canonical `A@2` node;
- source payloads are linked rather than recursively copied;
- a second materialization reuses the same generation and digests;
- copy mode rejects the cycle before creating a module tree; and
- the entire check passes on both Linux and macOS.

The black-box suite is separate from product unit tests. CI also reruns the
product's focused executable cycle test at the exact same commit so a mismatch
between the independent canary and the checked-in product regression is visible.

## Current product boundary

The exact graph materializer consumes a canonical resolved
`zpkg/dependency-graph/v1` document or a local source-binding plan. Automatic
conversion from ordinary `.zpkg.toml` constraints into this exact graph and the
corresponding lockfile-v2 integration remain follow-up work; this canary does
not claim that ordinary `zed install` has completed that migration.
