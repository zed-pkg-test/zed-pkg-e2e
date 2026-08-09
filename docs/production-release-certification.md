# Production registry release certification

The production release is evidence-driven. A workflow run is not successful merely because a deployment or a publish command was attempted; every immutable source, image, database migration, package version, and R2 object must be tied to one ledger.

## Immutable component set

The release ledger records exact commits and image digests for:

- `zed-lib-core`, including the canonical shared-definition revision
- `zed-api-server.rs` runtime and migration image
- `zed-web-server.rs`
- `shared-auth-server.rs`
- `zed-cli`
- `zed-infra` and the Argo CD application revision

The migration job uses the same API image digest as the runtime and completes before either API or web replicas are promoted. Long-running replicas run with automatic migration disabled.

## Full-stack certification

The test stack uses one PostgreSQL instance for both machine publication and the canonical `zed_*` projection. It proves:

- Shared Auth authorization code + S256 PKCE + exact callback
- single-use code redemption and replay rejection
- origin-scoped signed application session
- delegated `zed-pkg` audience and `zpkg:account` scope
- account pages and API mutations observe the same canonical rows
- private packages remain invisible outside their memberships
- private-to-public promotion succeeds at exactly 10 days and 50 downloads, and fails above either limit
- machine publication creates the package, immutable version, verified R2 upload ledger, and audit fact before returning success
- identical publish retries are idempotent and divergent same-version facts are rejected

## Package and R2 evidence

The inventory resolves 16–25 root `.zpkg.toml` repositories, including `zed-lib-core`, and requires each manifest's release tag to peel to the exact recorded commit. Publication is dependency ordered and uses one pinned Zed CLI binary.

For every package version the ledger contains:

- repository, commit, tag, manifest identity, version, and archive format
- API package and version metadata
- artifact SHA-256, byte length, and download URL
- downloaded artifact SHA-256 and successful archive listing
- direct R2 `HEAD` for `artifacts/<sha256>.<format>` in the configured bucket
- clean install, lockfile digest, uninstall, frozen reinstall, and post-reinstall digest
- first-publish or already-identical result plus a verified idempotent retry conflict

At least one tar-family artifact and one ZIP artifact must be certified across the production and format-fixture ledgers. Secrets are available only to a protected production environment; pull-request workflows validate logic and immutable source pins without receiving credentials.
