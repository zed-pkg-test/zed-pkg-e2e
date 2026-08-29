# DEN-2995 — generated Rust enum wire-value canary

This independent `zed-pkg-test` lane observes one immutable
`ORESoftware/k8s-libs-and-shared-defs` source commit and exercises the generated
Rust crate as an external consumer.

## Contract

SQL enum labels are wire values, not formatting hints. Generated Rust Serde must
round-trip the exact source label, including separators and leading digits.
`ssh-ed25519` must never become `sshed25519`; `in_progress` must never become
`inprogress`.

The workflow:

1. reads the exact source repository and SHA from
   `fixtures/den-2995-rust-enum-wire-source.json`;
2. checks out that immutable commit with read-only permissions and no persisted
   credentials;
3. runs the authoritative generator drift check;
4. inspects the generator policy;
5. injects a black-box integration test into the generated Rust crate and runs
   it with Cargo;
6. records whether the pinned source is deliberately pre-fix or expected to be
   repaired.

## Promotion sequence

The initial pin has `expect_fixed: false`. It is retained only to prove that the
canary detects the already-merged defect rather than silently passing a broken
implementation.

Before promotion:

- repin `sha` to the exact repaired source PR head;
- change `expect_fixed` to `true`;
- require the exact-head workflow to pass;
- preserve the immutable source SHA and resulting run as Linear/GitHub evidence.

The source fix belongs in the pg-defs generator. Hand-editing generated Rust is
not acceptable evidence and will fail `generate.mjs --check`.

## Non-goals

This repository does not publish packages, modify the source branch, apply
Postgres DDL, use a personal access token, or carry provider, Linear, Cloudflare,
or R2 credentials.
