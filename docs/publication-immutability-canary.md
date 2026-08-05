# Publication immutability canary

This canary certifies the registry behavior required by deterministic builders
such as Nix and by every native-registry export that treats a public version as
an immutable identity.

## Contract

For one `{org, name, version}` publication:

1. the first upload creates content-addressed artifact and version metadata;
2. an exact retry is idempotent and changes no registry byte;
3. a retry with changed artifact bytes and the same package identity fails;
4. the failed retry changes no metadata, artifact, yank state, or index object;
5. the artifact referenced by the original metadata still matches its SHA-256.

The test does not accept “last writer wins.” Replacing bytes behind a version
would invalidate `.zpkg.lock`, Nix fixed-output derivations, remote caches, and
native-registry attestations.

## Isolation and reproducibility

`.github/workflows/publication-immutability.yml` builds one exact `zed-cli`
commit, verifies the binary checksum, and tests immutable commits of both a Node
and Rust fixture. Each matrix job owns a fresh directory-backed `file://`
registry and fresh Zed homes.

The canary clears `ZED_PKG_TOKEN`, disables interactive Git authentication,
never contacts the public Zed registry, and checks that the fixture checkout
remains clean. Third-party Actions, the CLI, interfaces crate, and fixtures are
all pinned to full commit SHAs.

## Test sequence

`scripts/publication_immutability.py` performs the following black-box sequence:

- publish the fixture;
- verify metadata and content-addressed artifact SHA-256;
- snapshot every registry file;
- republish the identical source and require the same complete snapshot;
- locate a source file that is actually present in the emitted tarball;
- mutate that file while preserving package name and version;
- pack again and prove that the candidate artifact digest changed;
- attempt the same-version publication and require failure;
- require the complete registry snapshot to remain unchanged;
- re-verify the original metadata and artifact digest.

A JSON evidence summary and command log remain under the job work root. On
failure, the workflow uploads the source status, registry checksums, tree
listing, logs, and summary for inspection.

## Local invocation

Build the selected CLI, then run against a clean fixture checkout:

```bash
python3 scripts/publication_immutability.py \
  --fixture-dir ../node-lib \
  --zed ../zed-cli/target/release/zed \
  --work-root /tmp/zed-publication-immutability
```

The work root must not already exist. It is intentionally retained after the
run so the registry and evidence can be audited.
