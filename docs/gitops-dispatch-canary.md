# Independent `zed gitops` external-dispatch canary

This canary certifies the root-command dispatch layer introduced for DEN-2725 against one exact immutable `zed-pkg/zed-cli` commit. It runs in the `zed-pkg-test` organization so the evidence is independent of the product repository and its branch-local tests.

The current product authority is `zed-pkg/zed-cli#233`. The earlier #230 branch was intentionally closed as a duplicate after #233 incorporated the same safe sibling-dispatch model plus reviewed trailing-root-option, literal-double-dash, and malformed-boolean behavior.

## Certified boundary

On Ubuntu 24.04, macOS 15, and Windows Server 2025, the workflow:

1. checks out the exact 40-character product commit without persisted credentials;
2. runs rustfmt, the focused external-subcommand unit and integration suites, and strict Clippy;
3. builds exact release `zed` and `zed-gitops` binaries;
4. compiles a standard-library-only probe executable used as an untrusted external command; and
5. runs independent black-box harnesses against copied binaries and disposable Git repositories.

The harnesses prove 18 behavior and security contracts:

- the Zed package installs both sibling executables and its smoke test exercises root dispatch;
- root help and Bash completion expose `gitops validate`;
- `zed gitops ...` and `zed help gitops` reach the sibling validator;
- direct and root-dispatched validation return identical structured reports for a valid exact-gitlink catalog;
- online-mode omission fails explicitly until online evidence exists;
- pin drift propagates policy exit code 2 and the `source.pin-drift` diagnostic;
- a missing documented validator fails closed with an actionable message;
- the current working directory and relative `PATH` entries are never searched;
- the sibling `zed-gitops` executable wins over a same-named executable in an absolute `PATH` directory;
- arbitrary arguments are not rewritten by a shell;
- explicitly supplied root options before the external command become the expected child environment variables;
- child exit status is preserved;
- a `zed-install` executable cannot shadow the built-in `install` command;
- recognized root options after the external command are lifted from child argv into canonical child environment;
- a literal `--` terminates root-option extraction and preserves all following child arguments; and
- malformed trailing root booleans fail closed without executing the extension.

Each platform emits two commit-addressed JSON evidence records under schemas:

```text
zed-pkg-test/gitops-dispatch-canary/v1
zed-pkg-test/gitops-dispatch-trailing-options/v1
```

The evidence contains only the immutable candidate, platform identity where applicable, binary SHA-256 values, and passed check names. It does not record command environments or credentials.

## Isolation

The workflow has read-only repository permission and all third-party Actions are pinned to immutable commits. Product and harness checkouts use `persist-credentials: false`. The black-box subprocess environment removes token, secret, password, private-key, access-key, API-key, authorization, cookie, and inherited Zed credential variables before invoking either binary or the probe.

The fixture uses only a disposable local Git index and a synthetic mode-160000 gitlink. It performs no clone, push, registry access, Kubernetes mutation, Argo CD reconciliation, Cloudflare API call, DNS change, R2 access, or persistent namespace write.

## Coordination

- Product implementation: `zed-pkg/zed-cli#233`
- Closed superseded product lane: `zed-pkg/zed-cli#230`
- Standalone validator baseline: `zed-pkg/zed-cli#224`
- Real merged cluster-contract certification: `zed-pkg-test/zed-pkg-e2e#121`
- Linear: `DEN-2725`
- Portfolio roadmap: `ORESoftware/k8s-cluster#1097`

This canary certifies command discovery and process boundaries. It does not activate the inert ApplicationSet pilot and does not claim online repository reachability, cluster access, or deployment authority.