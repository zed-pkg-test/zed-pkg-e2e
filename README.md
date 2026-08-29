# zed-pkg-e2e

End-to-end verification for the **zed-pkg-test** fixture organization. This
repository owns three complementary suites:

1. browser automation against a running zed registry stack;
2. a stateless GitHub Actions matrix that exercises package author and consumer
   lifecycles across every repository in `zed-pkg-test`; and
3. pinned cross-repository acceptance canaries for CLI behavior that must be
   proven with real package-author and consumer repositories before release.

## GitHub API fallback (registry down, GitHub up)

`.github/workflows/github-api-fallback.yml` calls `api.github.com` for real.
`scripts/github_api_fallback.py` publishes a packed tarball to
[`zed-pkg-test/github-api-fallback-canary`](https://github.com/zed-pkg-test/github-api-fallback-canary)
Releases, then downloads the same sha256 from the public
`/releases/download/` URL with no `registry.zpkg.net` hop. Optional `--zed`
proves `zed install --frozen` against a closed loopback registry still
materializes that payload (needs zed-cli#294). Pull requests only compile the
script; `workflow_dispatch` runs the live GitHub APIs. See
`docs/github-api-fallback-canary.md`.

`scripts/ghcr_fallback.py` is the complementary Packages proof: it publishes
the packed tarball only to
[`ghcr.io/zed-pkg-test/ghcr-fallback-canary`](https://github.com/zed-pkg-test/ghcr-fallback-canary)
(no Release) so `zed install --frozen` must take the GHCR fallback branch.
See `docs/ghcr-fallback-canary.md`.

Cloudflare Workers in [`zed-pkg/zed-infra`](https://github.com/zed-pkg/zed-infra)
(`workers/registry-proxy`, `workers/cdn-proxy`) reconstruct the same GitHub
URLs at `registry.zpkg.net` / `cdn.zpkg.net` when the k8s origin is 502.

## How this differs from `zed-pkg/zed-e2e`

[`zed-pkg/zed-e2e`](https://github.com/zed-pkg/zed-e2e) owns registry-stack
orchestration: it boots Postgres and both Rust servers, seeds synthetic
packages, and drives the system with Playwright, Puppeteer, and Selenium. That
is the right boundary for registry semantics and browser-stack integration.

This repository uses the real fixture trees. It does not duplicate the server
boot sequence; browser tests attach to the stack that `zed-pkg/zed-e2e` owns,
while the lifecycle matrix uses a fresh directory-backed `file://` registry in
each job. The latter needs no server, account, token, or persistent runner
state.

## Full lifecycle matrix

`.github/workflows/lifecycle.yml` covers all 22 repositories in the organization:
20 package fixtures plus the deliberately non-package `shared-schema` repository
and this orchestration repository as fail-closed negative cases. Workspace
members and polyglot target packages are expanded and tested independently.

Each matrix job starts with an empty runner directory and owns its own registry,
zed home, dependency clones, R2G workspace, consumers, lockfiles, and diagnostic
artifacts. The harness never publishes to the public service and never relies on
an artifact from a previous job or workflow run.

For every publishable fixture, `scripts/lifecycle.py` verifies:

- credential-free `zed release plan --json` output;
- byte-for-byte deterministic packing in two independent output directories;
- `zed r2g` round-trip installation against a private dependency-registry
  snapshot, without mutating the input registry;
- no-write `zed publish --dry-run` behavior;
- actual `file://` publication followed by byte-stable idempotent republishing;
- registry metadata and artifact SHA-256 integrity;
- discovery through `zed find`;
- cold copy-mode installation with no symlinks;
- lockfile-identical `zed install --frozen` from a fresh home;
- yank semantics: new resolution fails, locked frozen installation still works,
  and `--undo` restores fresh installation; and
- a clean fixture Git worktree after every phase.

Dependency edges are explicit and recursively seeded in the same job. For
example, an app fixture first publishes its library fixture to the job-local
registry; `workspace-monorepo` is exercised as the root plus `ws-core`,
`ws-utils`, and `ws-cli`; and one polyglot source publication is checked through
each derived target package.

The workflow uses read-only repository permissions, disables persisted checkout
credentials, pins third-party actions and zed components to immutable commit
SHAs, builds the zed binary once, verifies its checksum in every matrix job,
uses bounded parallelism, and uploads diagnostics only on failure. It runs for
pull requests and `main`, supports an explicit dispatch with a SHA-pinned CLI,
and performs a weekly cross-repository drift check.

Run one repository locally:

```bash
cargo build --release --manifest-path ../zed-cli/Cargo.toml --bin zed
python3 scripts/lifecycle.py \
  --repo node-app \
  --fixture-dir ../node-app \
  --zed ../zed-cli/target/release/zed \
  --work-root /tmp/zed-lifecycle-node-app
```

The work root is disposable. Reusing the same path is safe because the harness
requires a fresh root and GitHub-hosted jobs receive a new runner filesystem.


## GitHub / R2 fallback (registry down, CDN up)

`.github/workflows/github-r2-fallback.yml` is a reusable canary for the
`cdn.zpkg.net` guessable-object contract. `scripts/github_r2_fallback.py`
publishes a disposable package to a `file://` registry, copies the tarball onto
`packages/` and `github/` keys, points `--registry` at a closed loopback port,
and proves `zed install --frozen` restores the packed payload from a loopback
CDN. It also round-trips the typed `get_version` RPC frame over TCP NDJSON
(from `github.com/oresoftware/api-docs`). Pull requests only compile the
script; a full product run needs exact `zed-cli` and `zed-interfaces` commits
via `workflow_call` or `workflow_dispatch`. See
`docs/github-r2-fallback-canary.md`.

## Durable first-install acceptance

`.github/workflows/durable-first-install.yml` is the cross-organization
acceptance gate for DEN-1413. It builds one immutable `zed-cli` commit on Linux
and macOS, publishes the exact pinned `zed-pkg-test/node-lib` fixture to a fresh
`file://` registry, removes Zed state from the pinned `node-app` fixture, and
installs the real package from a nested consumer directory.

`scripts/durable-first-install.sh` verifies that:

- a dependency-bearing first install creates `.zpkg.toml` at the inferred
  native-project root, not in the nested invocation directory;
- two independently created consumers with the same basename and inputs receive
  byte-identical manifests and lockfiles;
- the generated manifest records direct dependency, target, adapter, marker,
  and deterministic local identity metadata;
- copy-mode package and Node adapter outputs contain no symlinks and the real
  Node application executes successfully;
- an inferred local consumer cannot be published, even with dry-run and VCS
  checks skipped;
- `--do-not-write-new-manifest` is an informational no-op for generated and
  authored existing manifests;
- the canonical flag and `ZED_PKG_DO_NOT_WRITE_NEW_MANIFEST` preserve explicit
  ephemeral installs, while `--skip-manifest` remains a deprecated compatibility
  spelling;
- failed resolution removes the exact generated manifest and leaves no lock,
  package tree, adapter output, or transaction debris; and
- lock-only frozen restoration fails by default and succeeds byte-for-byte only
  when no-new-manifest intent is explicit.

All component refs and actions are immutable. During implementation review, the
workflow pins the exact head of the corresponding `zed-cli` pull request. The
pin must move to that PR's final reviewed head after every implementation
change, then to the merge commit when the CLI work lands. The workflow has
read-only repository permissions and uploads only bounded local-registry
diagnostics on failure.

Run the acceptance harness locally with sibling fixture checkouts:

```bash
cargo build --release --manifest-path ../zed-cli/Cargo.toml --bin zed
bash scripts/durable-first-install.sh \
  ../zed-cli/target/release/zed \
  ../node-lib \
  ../node-app \
  /tmp/zed-durable-first-install
```

### Polyglot durable first-install acceptance

`.github/workflows/durable-first-install-polyglot.yml` verifies the same default
manifest-creation contract against immutable real Go, Python, and Rust
app/library pairs on Linux and macOS. It is intentionally separate from the full
lifecycle matrix because it removes each consumer's authored `.zpkg.toml` before
installation and treats the resulting generated manifest as the artifact under
test.

For every ecosystem, `scripts/durable-first-install-polyglot.sh`:

- publishes the pinned library to a fresh credential-free `file://` registry;
- invokes `zed install` from a nested directory below the native project root;
- verifies that one deterministic, non-publishable `.zpkg.toml` is created only
  at the inferred root;
- checks inferred target and adapter metadata plus the direct dependency;
- compares an explicit `@^1.0.0` install with an unversioned latest-resolution
  install and requires byte-identical manifest and lockfile output;
- executes the real native application through Go, Python, or Cargo;
- proves copy-mode output contains no symlinks;
- uninstalls materialized content while retaining managed state; and
- performs a frozen nested-directory reinstall without changing manifest or
  lockfile bytes.

All source repositories, the CLI implementation, the shared interface contract,
and third-party actions are pinned to immutable commit SHAs. Each matrix entry
owns isolated registry, home, build, consumer, and diagnostics directories.

Run one ecosystem locally after building the selected CLI:

```bash
bash scripts/durable-first-install-polyglot.sh \
  ../zed-cli/target/release/zed \
  go \
  ../go-lib \
  ../go-app \
  /tmp/zed-durable-go
```

## Browser suites

| Suite | What it covers |
| --- | --- |
| `suites/fixture-package-pages.spec.ts` | Each single-language fixture (`node-lib`, `rust-lib`, `go-lib`, `python-lib`) renders the version, description, repository URL, and install snippet persisted from its `.zpkg.toml`; each is findable through HTMX search; a missing package is a real 404. |
| `suites/polyglot-fan-out.spec.ts` | `polyglot-lib` becomes four separately addressable packages with distinct content hashes, and the unsuffixed repository name is not itself a package. Ecosystem-specific install enforcement remains in the lifecycle matrix, which reads each derived artifact manifest and exercises the native toolchain. |

## Managed application lifecycle certification

`.github/workflows/formal-app-lifecycle.yml` pins the exact production
`zed-pkg/zed-sync` commit for DEN-4181. It verifies immutable commit and artifact
provenance, cross-checks the registered Quint model against the canonical trace
and JSON Schema, requires Rust, JavaScript, and Dart to consume the same fixture,
and executes the production repository's complete pinned verification matrix.

Run the cross-repository structural check against a sibling checkout:

```bash
python3 scripts/verify_formal_app_lifecycle_contract.py ../zed-sync
python3 -m unittest -v tests/test_formal_app_lifecycle_contract.py
```

The browser workflow pins the stack, CLI, interfaces, Rust servers, and fixture
inputs to immutable commit SHAs. Both Node workspaces install from their checked-in
lockfiles with `npm ci`. The fixture publisher forces the local API URL, uses a
disposable zed home, clears ambient credentials, and mints owner tokens scoped to
each fixture manifest org through the local test database. It never falls back
to the public registry or a developer's saved state.

### Run browser automation

```bash
# 1. Bring up the stack through the suite that owns it.
cd ../zed-e2e && npm ci --ignore-scripts && npm run stack:up && cd -

# 2. Install browser-test prerequisites from the lockfile.
npm ci --ignore-scripts
npm exec -- playwright install chromium

# 3. Publish the fixture inputs and drive the UI.
npm run e2e
npm run e2e:fixtures
npm run e2e:polyglot
```

Sibling checkouts are expected alongside this repository: `zed-cli`,
`zed-interfaces`, `zed-api-server.rs`, `zed-web-server.rs`, and the fixture
repositories the browser tests publish (`node-lib`, `rust-lib`, `go-lib`,
`python-lib`, and `polyglot-lib`). The lifecycle harness instead clones
prerequisites into its disposable work root according to the explicit dependency
graph.

## Browser configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `ZED_E2E_API_URL` | `http://127.0.0.1:48080` | Local API server whose health and package metadata are asserted. |
| `ZED_E2E_WEB_URL` | `http://127.0.0.1:48081` | Local web server the browser drives. |
| `ZED_E2E_DATABASE_URL` | `postgres://zed:zed@127.0.0.1:55432/zed_e2e` | Test database used only to mint scoped fixture tokens. |
| `ZED_E2E_HOME` | process-specific directory under the OS temp root | Disposable zed store and credentials root. |
| `ZED_BIN` | `../zed-cli/target/debug/zed` | zed binary used to publish browser fixtures. |
| `ZED_E2E_API_BIN` | `../zed-api-server.rs/target/debug/zed-api-server` | API binary used to create scoped test tokens. |
| `PW_CONNECT_WS` | — | Connect Playwright to a remote browser server instead of local Chromium. |

Defaults match `zed-pkg/zed-e2e`, so the common local case needs no additional
environment variables after the sibling stack is running.

## License

MIT
