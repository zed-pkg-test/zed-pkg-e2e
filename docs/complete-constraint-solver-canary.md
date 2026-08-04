# Complete constraint-solver black-box canary

Linear: [DEN-1553](https://linear.app/denman/issue/DEN-1553/zed-cli-solve-overlapping-compatible-transitive-constraints-before)

Product candidate: `zed-pkg/zed-cli#89`.

## Purpose

This canary verifies the complete one-version dependency solver through the
public `zed` executable, immutable package artifacts, an isolated `file://`
registry, real lockfile publication, and normal symlink materialization. It is
separate from the solver's in-crate tests so a graph-selection result cannot
pass only because an internal helper and its test share the same mistake.

## Certified graphs

### Overlapping compatible ranges

```text
consumer
├── overlap-left@1.0.0  -> overlap-shared ^1
└── overlap-right@1.0.0 -> overlap-shared <=1.5.0

published overlap-shared: 1.5.0, 1.9.0
```

The only common solution is `overlap-shared@1.5.0`. The canary installs the
same consumer with opposite TOML dependency order and requires byte-identical
lockfiles. It then publishes `overlap-shared@1.10.0` and proves a fresh frozen
replay still consumes the exact locked `1.5.0` graph without re-solving.

### Multi-coordinate backtracking

```text
backtrack-a@1.1.0 -> backtrack-x =2.0.0
backtrack-a@1.0.0 -> backtrack-x =1.0.0
backtrack-b@1.1.0 -> backtrack-x =1.0.0
backtrack-b@1.0.0 -> backtrack-x =2.0.0
consumer -> backtrack-a ^1, backtrack-b ^1
```

Newest-first selection initially chooses incompatible `a@1.1.0` and
`b@1.1.0`. The stable solution is `a@1.1.0`, `b@1.0.0`, and `x@2.0.0`.

### Rejected-candidate constraint removal

The newest `stale-chooser` candidate contributes both a conflicting shared
version and an `stale-obsolete` dependency. A peer forces the other shared
version. The canary requires the solver to backtrack to the older chooser,
materialize `stale-replacement`, and omit `stale-obsolete` from both the lock
and project tree.

Candidate artifact downloads that occurred during search may remain in the
immutable global store. Only project materialization and the selected lock
graph are required to exclude rejected candidates.

### Deterministic unsatisfiable provenance

Two package paths require mutually exclusive exact versions of one shared
package. The canary runs opposite root declaration orders and requires:

- failure before project transaction or lockfile publication;
- both complete provenance paths in the diagnostic; and
- byte-identical normalized error output.

### Dependency cycles

A two-package cycle must terminate with exactly one selected version for each
coordinate and materialize both packages once.

## Acquisition and replay boundaries

The canary retains the existing recursive-installer contracts while exercising
the new solver:

- the reported worker bound remains five;
- selected artifacts use the existing per-SHA acquisition and global store;
- project output remains symlink-first;
- warm frozen replay downloads zero artifacts;
- fresh frozen replay uses the exact lock graph;
- failure does not create `.zpkg.lock`, `zed_modules`, or transaction staging.

The workflow uses one hosted Ubuntu job by default to conserve Actions usage.
A manual `run_arc_smoke` input repeats the same executable canary on the
registered `sonus-ci` self-hosted ARC scale set, providing a direct parity lane
for the AWS/Hetzner continuity infrastructure without making PR checks wait for
an unregistered runner.

## Promotion rule

Keep this pull request draft while the product candidate is still moving.
Whenever `zed-cli#89` advances, update the immutable `ZED_CLI_REF` only, rerun
this canary, and record the exact head and workflow evidence on DEN-1553.
Do not weaken graph or diagnostic assertions to make a candidate pass.
