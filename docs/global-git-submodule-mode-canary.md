# Global Git-submodule compatibility canary

This repository independently certifies the shared Git-submodule compatibility
switch merged from [`zed-pkg/zed-cli#150`](https://github.com/zed-pkg/zed-cli/pull/150).
The workflow pins the exact shipped merge commit:

```text
ff166471338e46729b7cf892b44240452a56ace6
```

and the exact public interface contract used by that commit:

```text
c2e049006453c26ca8ca291783f681fce75cb01f
```

## Contract boundary

The black-box harness builds disposable local Git repositories and invokes only
the compiled `zed` executable. It proves that one typed control surface works
consistently for cooperative installation and takeover:

- Git transport remains disabled by default;
- `zed --git-submodules install` initializes top-level and nested submodules;
- `zed install --git-submodules=false` overrides an inherited true environment;
- `ZED_PKG_GIT_SUBMODULES=yes zed install` enables recursive initialization;
- `zed --git-submodules overtake` adopts eligible `.gitmodules` entries into
  `.zpkg.toml` and `.zpkg.lock` with exact package and commit provenance;
- `zed overtake --git-submodules` and environment-only takeover are idempotent;
- explicit false disables takeover without mutating the manifest or lock; and
- root and takeover help expose the same option and environment key.

The merged implementation's focused Rust tests and real-process interoperability
test also run before the black-box contract. Linux additionally runs all-target
Clippy with warnings denied.

## Isolation

The Ubuntu 24.04 and macOS 15 jobs use process-local `file` Git transport and an
empty disposable `file://` package registry. They receive no Zed token, GitHub
token, persisted checkout credential, publication credential, or public
registry mutation capability. Action, product, and interface inputs are pinned
by immutable full commit SHA.

The existing twelve-check Git-submodule canary remains the broader certification
for takeover, mixed repositories, frozen replay, drift rejection, and rollback.
This focused lane is additive: it certifies the shared global CLI/environment
routing on the exact commit now present on `zed-cli` `main`.
