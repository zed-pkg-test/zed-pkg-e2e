# External Zed → Nix plan certification

This repository tests deterministic Nix export planning outside the `zed-cli`
implementation repository. `.zed-nix-plan-cli-ref` pins one exact 40-character
CLI commit. The workflow builds that commit and runs it against clean copies of
real `zed-pkg-test/node-lib` and `zed-pkg-test/polyglot-lib` fixtures.

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

The workflow has read-only repository permissions, uses commit-pinned actions,
disables persisted checkout credentials, accepts only a full immutable manual
CLI override, and uploads diagnostics only on failure.

Before this canary merges to `main`, `.zed-nix-plan-cli-ref` must point to the
final merged zed-cli planner commit. Scheduled or dispatch-driven certification
must never depend on a mutable branch or pull-request merge ref.
