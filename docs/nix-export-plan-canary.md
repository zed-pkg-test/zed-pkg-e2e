# External Zed → Nix plan certification

Tracking: DEN-1411, DEN-1418, DEN-1422

This repository tests deterministic Nix export planning outside the `zed-cli`
implementation repository. `.zed-nix-plan-cli-ref` pins one exact 40-character
CLI commit. The workflow builds that commit and runs it against immutable clean
checkouts of real `zed-pkg-test/node-lib` and `zed-pkg-test/polyglot-lib`
fixtures on Ubuntu 24.04 and macOS 15.

The reviewed inputs for this candidate are:

- `zed-pkg/zed-cli@d8a2217edde8fa8c0a51f1db92e6f0b2b77296f5` from current-main planner PR #82;
- `zed-pkg-test/node-lib@222cdf57f48530fce8e6c1f58632d9676203512e`;
- `zed-pkg-test/polyglot-lib@998964aa58e49595547650b4a8da407b7a2283a9`;
- `zed-pkg/zed-interfaces@c2e049006453c26ca8ca291783f681fce75cb01f`.

The candidate contains the merged install-shaped Zed→Nix bridge, pure Nix
evaluation ratchet, resolver-only `zed fetch`, resolver-only Nix FOD, and the
read-only planner. It is constructed from current `main` by a two-parent semantic
merge and contains only the eight planner-specific paths. It does not restore
obsolete Cargo pins, temporary branch-writing workflows, or stale fetch fixtures.

The canary proves:

- identical package bytes at different absolute paths emit identical compact
  `zed.nix-export-plan/v1` JSON;
- exact manifest and lock digests are retained;
- packed artifact identity is stable and path-independent;
- systems are normalized deterministically;
- a real prebuilt executable appears in source and packed artifact;
- token, Supabase key, registry credentials, home, and workspace paths do not
  enter the plan;
- planning never creates the configured global Zed home;
- fixture checkouts and disposable package copies remain unchanged;
- a manifest comment changes both exact-manifest and artifact identity;
- missing locks and source build hooks fail with actionable diagnostics;
- a polyglot package requires explicit target selection;
- target synonyms resolve to the same canonical target and plan;
- isolated Node and Rust targets are re-rooted to published identities; and
- different target payloads do not collapse to one artifact identity.

The workflow verifies that the built CLI and both fixtures resolve to exact pins,
that Cargo uses the reviewed immutable interface revision, and that all three
checkouts remain clean after the canary.

The workflow has top-level `contents: read`, uses only commit-pinned Actions,
disables persisted checkout credentials, accepts only a full immutable manual
CLI override, grants no OIDC or package-write permission, and uploads bounded
failure diagnostics only when an assertion fails. Its policy check scans the
entire workflow while constructing denylist tokens from fragments, avoiding
false self-matches without weakening checks against write permissions, pushes,
`pull_request_target`, secret inheritance, OIDC/package writes, or mutable
fixture refs.

Before this canary merges to `main`, `.zed-nix-plan-cli-ref` must be advanced to
the final merged planner commit. Scheduled or dispatch-driven certification must
never depend on a mutable branch or pull-request merge ref.
