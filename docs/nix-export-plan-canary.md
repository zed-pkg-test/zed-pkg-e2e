# External Zed → Nix plan certification

This repository tests deterministic Nix export planning outside the `zed-cli`
implementation repository. `.zed-nix-plan-cli-ref` pins one exact 40-character
CLI commit. The workflow builds that commit and runs it against immutable clean
checkouts of real `zed-pkg-test/node-lib` and `zed-pkg-test/polyglot-lib`
fixtures on Ubuntu 24.04 and macOS 15.

The candidate and fixture revisions are independent reviewed inputs:

- `zed-pkg/zed-cli@2b298d94c96fe74339da930013935b3d21039da0`;
- `zed-pkg-test/node-lib@222cdf57f48530fce8e6c1f58632d9676203512e`;
- `zed-pkg-test/polyglot-lib@998964aa58e49595547650b4a8da407b7a2283a9`;
- `zed-pkg/zed-interfaces@c2e049006453c26ca8ca291783f681fce75cb01f`.

The candidate contains the current resolver-only CLI, resolver-only Nix FOD,
deterministic planner, merged install-shaped bridge, and the four-system public
Nix evaluation ratchet. The stacked histories are explicit; the candidate does
not restore obsolete Cargo pins or temporary branch-writing workflows.

The canary proves:

- identical package bytes at different absolute paths emit identical compact
  `zed.nix-export-plan/v1` JSON;
- the exact manifest and lock digests are retained;
- the packed artifact digest is stable and path-independent;
- systems are normalized deterministically;
- a real prebuilt executable appears in both the source and packed artifact;
- global token, Supabase key, registry, home, and workspace paths do not enter
  the plan;
- planning never creates the configured global Zed home;
- source fixture checkouts and disposable package copies remain unchanged;
- a manifest comment changes both exact-manifest and artifact identity;
- missing locks and source build hooks fail with actionable diagnostics;
- a polyglot package requires explicit target selection;
- target synonyms resolve to the same canonical target and plan;
- isolated Node and Rust targets are re-rooted to their published identities;
  and
- different target payloads do not collapse to one artifact identity.

The workflow also verifies that the built CLI and both fixtures resolve to their
exact pins, that Cargo uses the current immutable interface revision, and that
all three checkouts remain clean after the canary.

The workflow has top-level `contents: read`, uses only commit-pinned Actions,
disables persisted checkout credentials, accepts only a full immutable manual
CLI override, grants no OIDC or package-write permission, and uploads bounded
failure diagnostics only when an assertion fails. It rejects mutable fixture
branch refs such as `main` in its own policy check.

Before this canary merges to `main`, `.zed-nix-plan-cli-ref` must point to the
final merged zed-cli planner commit. Scheduled or dispatch-driven certification
must never depend on a mutable branch or pull-request merge ref.
