# Production package roundtrip

`production-package-roundtrip.yml` is the fail-closed acceptance lane for
DEN-2788. It uses one immutable `zed-cli` revision against the real Rust
`zed-api-server`, PostgreSQL, and the server's artifact-storage contract.

The release ledger is dependency-closed and ordered. It contains 19 exact
repository commits across `zed-pkg`, `shared-auth`, and `ORESoftware`, including
`zed-lib-core`, its shared schema dependency, and the Shared Auth packages that
close that dependency graph.

For every package the lane:

1. fetches the exact source commit and verifies any declared release tag;
2. re-parses `.zpkg.toml` and verifies identity, version, repository, and
   dependencies against the ledger;
3. publishes through `zed publish` and verifies API metadata plus downloaded
   artifact SHA-256 and archive readability;
4. repeats the publish and requires immutable idempotency;
5. installs the complete graph with `zed install`, verifies all 19 package
   manifests, uninstalls it, and restores the same graph with `--frozen`.

The PR lane uses local artifact storage so it is deterministic and does not
write production. The same script is suitable for a protected Kubernetes/R2
lane by pointing `--registry` at the deployed API and supplying a short-lived
registry token. Production publication must retain the exact ledger, CLI, API,
and evidence artifact; it may not substitute mutable branch heads.
