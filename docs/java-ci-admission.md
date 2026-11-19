# Java exact-tree CI admission

This gate checks exact source commit and tree identity, a clean checkout, and tracked regular-file proof inputs **before** compilation. It rejects the wrong expected head SHA, dirty workspaces, symlinks in proof paths and untracked files. It emits `ATTO_EXACT_TREE_GATE=PASS` only after all checks.

This is *admission*, not executable guest evidence. A green job requires at least one **executed test step**, and language claims additionally require compiled `.atto` JVM and Graal Native Image evidence with positive and negative source fixtures. An Actions record with `steps=[]`, a skipped job, a pending job or a status-only marker is **not passing evidence**.

To test a private source on another organization, copy the full Git tree using the GitHub connector, re-create missing blobs by content and verify their exact Git SHA, and confirm the *entire carrier tree SHA equals* the approved source tree SHA. Use carrier-local checkout only, no cross-org access secrets. Inspect job steps and logs; do not merge into the carrier's primary branch.

This source-local gate does not enable self-hosted runners, configure billing, or claim full Java, LLVM or Wasm parity. Related Oreslang security context: [Java #3179](https://github.com/ores-truffle-oreslang/oreslang-source.java/pull/3179), [Java #3191](https://github.com/ores-truffle-oreslang/oreslang-source.java/pull/3191).
