# Complete constraint-solver black-box canary

Linear: [DEN-1553](https://linear.app/denman/issue/DEN-1553/zed-cli-solve-overlapping-compatible-transitive-constraints-before)

Product candidate: `zed-pkg/zed-cli#89` at immutable commit
`ddf0487f560c19000e2844b62797f1cd6299c26e`.

The selected-only overlap assertions were restored and proven on certification
head `05d8eeb4d42957ca6e01e3829798ff2eb285dd95`. That exact head built the final
product and passed both public-CLI canaries before publishing the permanent
script and deleting its temporary helper. This document-only evidence commit
then triggers the final read-only repository matrix under the connected GitHub
app.

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
lockfiles. Each cold solve must report exactly
`(resolved=3, workers=5, downloaded=3)`: both root packages plus the selected
shared package, with no speculative acquisition of rejected
`overlap-shared@1.9.0`. It then publishes `overlap-shared@1.10.0` and proves a
fresh frozen replay still consumes the exact locked `1.5.0` graph without
re-solving.

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
version and a `stale-obsolete` dependency. A peer forces the other shared
version. The canary requires the solver to backtrack to the older chooser,
materialize `stale-replacement`, and omit `stale-obsolete` from both the lock
and project tree.

A candidate required by an active branch may enter the immutable global store
before later backtracking rejects that branch. It must never appear in
`.zpkg.lock` or the consumer project. The overlap graph has a stricter ordering
contract: unresolved shallower parents must contribute their constraints before
the deeper shared coordinate is acquired, so the rejected `1.9.0` archive does
not enter either cold home. Yanked candidates have the strictest boundary:
immutable metadata must reject them before archive acquisition.

### Deterministic unsatisfiable provenance

Two package paths require mutually exclusive exact versions of one shared
package. The canary runs opposite root declaration orders and requires:

- failure before project transaction or lockfile publication;
- both complete provenance paths in the diagnostic; and
- byte-identical normalized error output.

### Dependency cycles

A two-package cycle must terminate with exactly one selected version for each
coordinate and materialize both packages once.

## Acquisition, policy, and replay boundaries

The canary retains the existing recursive-installer contracts while exercising
the new solver:

- the reported worker bound remains five;
- equal-depth cold frontiers can reach the bounded acquisition pool instead of
  serializing candidate downloads;
- unresolved shallower parents are selected before deeper candidate acquisition;
- the canonical overlap graph downloads exactly its three selected artifacts;
- selected artifacts use the existing per-SHA acquisition and global store;
- project output remains symlink-first;
- warm frozen replay downloads zero artifacts;
- fresh frozen replay uses the exact lock graph;
- failure does not create `.zpkg.lock`, `zed_modules`, or transaction staging;
- a freshly solved graph whose only common version is yanked fails with
  `--frozen` guidance;
- the yanked archive SHA never enters a fresh home before that failure; and
- an existing exact lock remains authoritative after the selected version is
  subsequently marked yanked.

The product's ordinary integration suite separately retains the opaque-version
compatibility boundary: opaque package identifiers are exact-only and a semver
range such as `^1` cannot match an opaque tag such as `legacy-api`.

The workflow uses one hosted Ubuntu job by default to conserve Actions usage.
A manual `run_arc_smoke` input repeats the same executable canary on the
registered `sonus-ci` self-hosted ARC scale set, providing a direct parity lane
for the AWS/Hetzner continuity infrastructure without making PR checks wait for
an unregistered runner.

## Promotion rule

Keep this pull request draft until all of the following are true on the exact
product commit above:

1. the hosted black-box canary passes without weakened assertions;
2. every inherited lifecycle, recursive, browser, durable-manifest, and mise
   workflow in this repository passes on the certification head;
3. every product workflow passes on the same immutable `zed-cli` SHA;
4. no unresolved review thread or change request remains; and
5. this certification PR merges before `zed-pkg/zed-cli#89`.

Whenever the product head advances, update the immutable `ZED_CLI_REF` and this
document together. Branch names and abbreviated SHAs are not valid promotion
evidence.
