# zed-pkg-e2e

End-to-end verification for the **zed-pkg-test** fixture organization. This
repository owns two complementary suites:

1. browser automation against a running zed registry stack; and
2. a stateless GitHub Actions matrix that exercises package author and consumer
   lifecycles across every repository in `zed-pkg-test`.

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
21 package fixtures plus this orchestration repository as the one fail-closed
negative case. `shared-schema` is a real language-neutral Zed package and is
also seeded recursively when Python or polyglot consumers declare
`zedtest/shared-schema`. Workspace members and polyglot target packages are
expanded and tested independently.

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

The lightweight `.github/workflows/lifecycle-harness-contract.yml` gate compiles
the Python harness and unit-tests repository classification, dependency-section
normalization, and dependency-source metadata before the full matrix has to
exercise those mappings against live fixture repositories.

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

## Browser suites

| Suite | What it covers |
| --- | --- |
| `suites/fixture-package-pages.spec.ts` | Each single-language fixture (`node-lib`, `rust-lib`, `go-lib`, `python-lib`) renders the version, description, repository URL, and install snippet persisted from its `.zpkg.toml`; each is findable through HTMX search; a missing package is a real 404. |
| `suites/polyglot-fan-out.spec.ts` | `polyglot-lib` becomes four separately addressable packages with distinct content hashes, and the unsuffixed repository name is not itself a package. Ecosystem-specific install enforcement remains in the lifecycle matrix, which reads each derived artifact manifest and exercises the native toolchain. |

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
