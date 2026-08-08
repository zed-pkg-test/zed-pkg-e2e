# Current-main root `zed gitops` certification

This canary independently certifies the clean current-main successor to DEN-2725:

```text
product PR: zed-pkg/zed-cli#242
candidate: 173bab46355ef715783d2734288ccc023c7f486b
exact parent: f226c2e57c8c830b21aedd4fb2eb505c584dc7fd
```

Unlike the superseded replay harness, this workflow does not merge another branch, apply a correction script, or rewrite product source. It checks out the immutable product commit with its parent and fails before compilation unless:

1. the candidate SHA is exact;
2. its direct parent is exact; and
3. its complete delta is exactly these eight reviewed paths:

```text
.zpkg.toml
docs/gitops-validator.md
scripts/validate-zed-package-graph.sh
src/completion.rs
src/external_subcommands.rs
src/lib.rs
src/main.rs
tests/external_gitops_dispatch.rs
```

## Platforms and gates

The test-org workflow runs on Ubuntu 24.04, macOS 15, and Windows Server 2025. Every platform runs:

- Rust formatting;
- focused external-dispatch unit and integration tests;
- strict Clippy with warnings denied;
- the Zed package-graph validator;
- locked release builds of `zed` and `zed-gitops`;
- a standard-library-only hostile external-command probe; and
- the existing black-box dispatch and trailing-root-option suites.

The black-box checks cover sibling-command selection, no working-directory or relative-PATH search, sibling precedence over absolute PATH, argument and exit-code propagation, structured validator parity, missing-extension behavior, literal `--`, recognized root-option lifting, malformed boolean rejection, help/completion visibility, and installed package outputs.

## Credential and mutation boundary

The canary strips inherited token, secret, password, private-key, access-key, API-key, authorization, cookie, and Zed credential variables from subprocesses. It uses only disposable local directories and a local Git index fixture.

It performs no package publication, registry write, GitHub mutation, Kubernetes/Argo operation, Cloudflare request, external agent task, or persistent namespace write. Product source must remain byte-for-byte unchanged throughout the run.

Evidence is commit-addressed and records only the immutable candidate/parent, runner OS/architecture, binary SHA-256 values, and passed check names. A green result certifies this exact product commit only; any successor requires a new immutable pin.
