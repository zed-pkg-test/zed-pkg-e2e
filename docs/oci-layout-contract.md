# Local OCI image-layout certification

This suite certifies the credential-free local layout boundary added by
`zed-pkg/zed-cli#56`. It treats the Rust implementation as an untrusted producer:
the black-box harness walks the generated OCI image-layout, hashes every blob
independently, and compares the result with a separate plan-only invocation.

The suite is stacked on the publication-plan contract in
`docs/oci-plan-contract.md`. Planning remains the authoritative identity model;
this follow-up proves that `--out` materializes those exact bytes without adding
network, authentication, or hidden project mutations.

## Exact inputs

`.github/workflows/oci-layout-contract.yml` pins:

- one exact 40-character `zed-pkg/zed-cli` candidate commit;
- immutable commits for `node-lib`, `node-app`, and `polyglot-lib` fixtures; and
- immutable commits for every third-party GitHub Action.

The job grants only `contents: read`, disables persisted checkout credentials,
and builds the candidate with its checked-in Cargo lockfile.

## Certified output

For each successful layout, the harness requires:

1. `oci-layout` contains image-layout version `1.0.0`;
2. `index.json` is an OCI image index with exactly one manifest descriptor;
3. the index descriptor digest equals the resolved publication reference and
   the plan-only manifest descriptor;
4. `org.opencontainers.image.ref.name` preserves the requested version tag;
5. every descriptor points to `blobs/sha256/<encoded digest>`;
6. every blob filename equals the SHA-256 of its bytes;
7. every descriptor size equals the actual byte count;
8. the OCI manifest carries the expected Zed config and typed package,
   source-manifest, and optional lockfile layers;
9. no undeclared or duplicate blob exists;
10. the reported blob count and total byte count match the independently walked
    layout; and
11. two separate materializations of the same package produce byte-identical
    trees apart from their requested output path.

## Fixture coverage

| Fixture | Boundary exercised |
| --- | --- |
| `node-lib` | deterministic layout, plan parity, full descriptor walk, syscall audit |
| `polyglot-lib` | one selected Rust target re-rooted from a four-language source repository |
| `node-app` | exact `.zpkg.lock` bytes preserved as a typed OCI layer |

The suite also creates a pre-existing output directory and proves the command
fails without changing its sentinel file.

## Source-tree and security boundaries

Before and after every command, the harness fingerprints the source fixture,
including relative paths, file bytes, symlink targets, and permission bits. It
also installs an intentionally unrecoverable `.zpkg-staging` sentinel. A passing
candidate must therefore leave the source tree unchanged, leave no `.zed/pack`
output, and avoid transaction recovery.

The representative `node-lib` materialization runs under `strace`. Any runtime
network socket syscall fails the job. Opening either poisoned saved-credentials
file also fails. Closed loopback proxies and nonexistent registry/auth endpoints
provide an additional guardrail.

## Formatting and executable validation

The workflow deliberately cannot push fixes back to the product branch. It:

1. runs `cargo fmt` on a disposable exact checkout;
2. records a patch and formatted source files as evidence;
3. runs focused OCI tests, Clippy with warnings denied, and a locked release
   build against the formatted tree;
4. runs the black-box contract; and
5. finally requires the originally pinned candidate to have produced no
   rustfmt diff.

A formatting-only failure therefore exposes the exact deterministic correction
without granting repository write permission. Product changes are reviewed and
committed in `zed-pkg/zed-cli`, then this canary is repinned to the new exact
commit.

## Evidence

Every run uploads formatting evidence, structured command results, layout
fingerprints, and the syscall trace for seven days. The evidence schema is
`zed-pkg-test.oci-layout-evidence/v1` and contains no registry token or account
secret.

## Local execution

Build the exact CLI candidate and place fixture checkouts beside this repository,
then run:

```bash
python3 scripts/oci_layout_contract.py \
  --zed ../zed-cli/target/release/zed \
  --strace "$(command -v strace)" \
  --node-lib ../node-lib \
  --node-app ../node-app \
  --polyglot-lib ../polyglot-lib \
  --evidence /tmp/zed-oci-layout-evidence
```

Linux with `strace` is required for the credential/network assertion.
