# `.gitmodules` metadata-boundary canary

This canary independently certifies the metadata hardening added to
`zed-pkg/zed-cli#110`. It pins exact CLI candidate
`0786987fe3a173659463940f4f905dc3b5bd19a8` and exact interface contract
`c2e049006453c26ca8ca291783f681fce75cb01f`.

The earlier package-boundary canary proves that initialized and clean submodule
source is packaged while nested Git control data is omitted. This follow-up
focuses on the trustworthiness of `.gitmodules` itself before any parser,
checkout, lock refresh, archive, or publication path consumes it. The candidate
also includes the exact rustfmt repair found by the first three-platform run.

## Unix black-box contract

Ubuntu 24.04 and macOS 15 build the exact locked release binary and exercise
real disposable Git repositories. The contract proves:

1. a committed stage-zero regular `.gitmodules` blob permits recursive
   `zed install --git-submodules`;
2. a symlinked `.gitmodules` rejects `zed pack` before archive creation;
3. the same symlink representation rejects install before submodule checkout or
   `.zpkg.lock` mutation;
4. a directory at `.gitmodules` rejects pack before Git parsing;
5. a stage-zero index entry with Gitlink mode `160000` rejects pack; and
6. an index containing conflict stages 1, 2, and 3 rejects install before
   checkout or lock mutation.

Each negative case requires a specific metadata-boundary diagnostic, verifies
that no archive or lockfile is left behind, and retains only a bounded transcript
and JSON record.

## Windows exact-head gate

Windows Server 2025 runs the focused metadata unit suite and all-target Clippy
with warnings denied against the same immutable CLI and interface commits. The
filesystem symlink attacks remain in the Unix black-box matrix because ordinary
Windows hosted runners do not provide the same unprivileged symlink semantics;
the platform-independent Git index-mode and stage validation is compiled and
executed by the Windows unit suite.

## Isolation

The workflow has read-only repository permissions, disables persisted checkout
credentials, removes inherited GitHub and Zed token variables from product
subprocesses, uses only disposable local file transport, and never contacts or
mutates a public registry.
