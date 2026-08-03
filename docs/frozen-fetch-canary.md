# External frozen-fetch canary

This repository validates `zed fetch --frozen` outside the `zed-cli`
implementation repository. The lifecycle workflow pins one immutable zed-cli
commit, builds it once, and reuses the checksummed binary for both the existing
22-repository matrix and a dedicated resolver-only fetch job.

The canary uses the real `zed-pkg-test/node-lib` package fixture and a disposable
`file://` registry. It performs this sequence:

1. publish the fixture through normal `zed publish`;
2. create a consumer lock through normal copy installation;
3. uninstall all project materialization while retaining exact lock bytes;
4. remove the install store;
5. run two independent frozen fetches with a separate home path;
6. compare every output file by relative path, byte length, and SHA-256;
7. verify `zed.fetch/v1` identity, artifact, provenance, source-classification,
   and lock-digest metadata;
8. prove the consumer tree and fixture checkout were unchanged;
9. prove the configured fetch home was never created;
10. reject a tampered artifact digest before final output publication;
11. reject an existing caller-owned destination without changing it;
12. reject a missing lock; and
13. export a deterministic dependency-free lock.

Literal registry paths and URLs must not appear in the portable index. Temporary
bundle and isolated-store directories must not survive success or failure.

The workflow uses read-only repository permissions, commit-pinned checkout and
artifact actions, an exact 40-character zed-cli revision, disabled persisted
checkout credentials, and failure-only diagnostics. The aggregate lifecycle
gate requires the external fetch canary and every fixture lifecycle to pass.

During development, `ZED_CLI_REF` points at the exact reviewed commit of the
stacked zed-cli pull request. Before this canary merges to `main`, update that pin
to the final merged zed-cli commit so scheduled and repository-dispatch runs
never depend on an unmerged branch head.
