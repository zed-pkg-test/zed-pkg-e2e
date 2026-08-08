# Current-main `zed inspect` certification

This canary independently certifies the canonical read-only IDE inspection contract:

```text
product PR: zed-pkg/zed-cli#244
candidate: b7036935ce3711563f1527472fc64e92bc1341aa
exact parent: 1ab18fcb2ff884e82af4cac4513d7b983a23c84a
```

The workflow uses one immutable product commit and one direct parent. It does not poll a mutable branch, merge another candidate, apply a source correction, or couple CLI promotion to any IDE adapter release.

Before compilation, every platform rejects the candidate unless its complete delta is exactly:

```text
schemas/inspect-v1.schema.json
src/cli_model.rs
src/inspect.rs
src/lib.rs
tests/inspect_cli.rs
```

The candidate preserves the previously merged `tool_profile` and `external_subcommands` modules and adds only the inspect protocol export.

## Three-platform quality and black-box contract

Ubuntu 24.04, macOS 15, and Windows Server 2025 each run:

- Rust formatting;
- focused inspect and CLI-model unit tests;
- compiled inspect CLI integration tests;
- strict focused Clippy with warnings denied;
- a locked release build of the exact `zed` candidate; and
- a standard-library-only adversarial black-box suite.

The black-box suite provides an unreachable registry, malformed saved credentials, fake environment and CLI token values, and a pending transaction sentinel. It requires:

- exactly one JSON document and no successful stderr;
- `offline=true`, `mutates_project=false`, and `loads_credentials=false`;
- stable recovery diagnostics and explicit suggested-action risk metadata;
- no token or malformed-manifest secret disclosure;
- byte-identical project and credential-home snapshots;
- no transaction recovery, materialization, or lockfile rewrite;
- help that succeeds without loading malformed credentials;
- global option values named `inspect` not triggering the command; and
- relative or missing roots failing without creating project or home state.

The malformed-manifest case assembles its fake credential-shaped value at runtime and requires a structured `MANIFEST_INVALID` diagnostic without echoing source text.

## Credential and mutation boundary

Every subprocess receives a scrubbed environment with token, secret, password, private-key, access-key, API-key, authorization, cookie, credential, and inherited Zed variables removed. Only disposable local project/home directories and an unreachable loopback registry are used.

The workflow performs no package publication, registry write, package installation, package-code execution, transaction recovery, GitHub or Linear mutation, provider invocation, Slack task execution, Kubernetes/Argo operation, Cloudflare request, SOPS decryption, or persistent namespace write.

Product and harness source trees must remain unchanged. Evidence contains only the immutable candidate/parent, runner OS/architecture, release-binary SHA-256, passed check names, and result.

A green result certifies this exact product commit only. Any successor requires a new immutable pin. Downstream VS Code interoperability is intentionally separate and must be repinned after the CLI contract itself merges.
