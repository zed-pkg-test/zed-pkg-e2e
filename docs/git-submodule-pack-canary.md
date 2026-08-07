# Git-submodule package-boundary canary

This workflow independently certifies the archive, publication, and Git-metadata
boundaries in `zed-pkg/zed-cli#110`. It invokes only the compiled public
executable and inspects resulting `tar.gz` bytes with Python's standard library.

The workflow pins exact CLI candidate
`0786987fe3a173659463940f4f905dc3b5bd19a8` and exact interface contract
`c2e049006453c26ca8ca291783f681fce75cb01f`. The candidate is the formatter-clean,
locally tested product head merged with current `main`, including mixed-submodule
takeover, Dart wiring, mise frozen identity, Windows test gating, atomic Nix
bundle behavior, conservative package exclusions, and indirect `.gitmodules`
metadata hardening. Ubuntu 24.04 and macOS 15 build that exact source, run the
focused product tests, and execute disposable local Git fixtures. Linux also
runs all-target Clippy with warnings denied.

## Certified behavior

The existing twelve-check archive contract uses a superproject containing a
Zed-package submodule, which itself contains a nested ordinary Git submodule. It
proves:
This contract independently certifies the archive and publication boundary in
`zed-pkg/zed-cli#110`. It invokes only the compiled public executable and
inspects the resulting `tar.gz` bytes with Python's standard library.

The workflow pins exact CLI candidate
`82c9d0e0b9e96402afa76f3b85c4b9e16c3cb101` and exact interface contract
`c2e049006453c26ca8ca291783f681fce75cb01f`. The candidate is the reviewed
semantic merge of the package guard with current `main`, including the latest
mixed-submodule, Dart-wiring, mise-identity, Windows test-gating, and atomic Nix
bundle changes, plus the contextual-error, conservative exclusion, and indirect
`.gitmodules` metadata hardening. Ubuntu 24.04 and macOS 15 build that source,
run the focused product tests, and execute disposable local Git fixtures. Linux
additionally runs all-target Clippy with warnings denied.

## Certified behavior

The fixture graph is a superproject containing a Zed-package submodule, which
itself contains a nested ordinary Git submodule. The black-box contract proves:

1. an included but uninitialized submodule rejects `zed pack` before an archive
   is created;
2. the same checkout rejects `zed publish --dry-run` before publication
   planning;
3. `zed install --git-submodules` initializes both submodule levels recursively;
4. a materialized checkout packages root, child, and nested runtime files;
5. no root or nested `.git`, `.gitmodules`, `.hg`, or `.svn` control data enters
   the archive;
6. the initialized package reaches a credential-free publication dry run;
7. a dirty included submodule fails before archive creation;
8. a dirty nested submodule also fails before archive creation;
9. an authored `publish.exclude = ["vendor/client/**"]` permits that entire
   subtree to remain uninitialized and omitted;
10. a project `.zedignore` with the same recursive boundary behaves identically;
11. a misleading `./vendor/client/**` pattern cannot bypass initialization; and
12. a misleading `/vendor/client/**` pattern cannot bypass initialization.

A separate three-check metadata contract proves:

13. a symlinked worktree `.gitmodules` rejects cooperative install before sync,
    lockfile, or materialized-module mutation;
14. a directory-backed worktree `.gitmodules` rejects pack before archive
    creation; and
15. a `120000` symlink entry in the Git index rejects pack even when the
    worktree `.gitmodules` is still a regular file.

Modern Git porcelain intentionally refuses to create the third hostile state.
The canary therefore rewrites one entry in a valid version-2 index, supports
both SHA-1 and SHA-256 repository formats, recomputes the index checksum, and
then proves `git ls-files --stage` accepts the resulting `120000` entry before
invoking Zed. This keeps the contract at the actual Git/product boundary instead
of mocking command output.
3. a symlinked `.gitmodules` rejects cooperative install before sync, lockfile,
   or materialized-module mutation;
4. a directory-backed `.gitmodules` rejects pack before archive creation;
5. `zed install --git-submodules` initializes both submodule levels recursively;
6. a materialized checkout packages root, child, and nested runtime files;
7. no root or nested `.git`, `.gitmodules`, `.hg`, or `.svn` control data enters
   the archive;
8. the initialized package reaches a credential-free publication dry run;
9. a dirty included submodule fails before archive creation;
10. a dirty nested submodule also fails before archive creation;
11. an authored `publish.exclude = ["vendor/client/**"]` permits that entire
    subtree to remain uninitialized and omitted;
12. a project `.zedignore` with the same recursive boundary behaves identically;
13. a misleading `./vendor/client/**` pattern cannot bypass initialization; and
14. a misleading `/vendor/client/**` pattern cannot bypass initialization.

An uninitialized submodule is optional only when one authored canonical
recursive rule conclusively excludes its complete subtree under the exact glob
syntax used by the packer. Sampling filenames, stripping path prefixes, changing
path separators, or otherwise normalizing an authored pattern is not treated as
proof that an unknown runtime file cannot enter the package.

Before any Git-submodule parser or mutating command runs, the worktree
`.gitmodules` entry must be a regular file. When tracked, its Git index entry
must also be a stage-zero regular blob; symlink, gitlink, conflict-stage,
directory, and other indirect representations fail closed.

## Isolation and evidence

Every Git repository, Zed home, registry directory, output directory, and
credential home is created below the runner's temporary work root. Both
contracts remove inherited GitHub and Zed token variables, disable interactive
Git authentication and system Git configuration, and enable local file
transport only through process-local Git configuration for their own fixtures.

No public registry, package namespace, account, token, container daemon, or
persistent runner state is used. Each operating-system job retains bounded
command transcripts and JSON evidence containing the exact binary version,
binary SHA-256, and all fifteen completed checks.
credential home is created below the runner's temporary work root. The contract
removes inherited GitHub and Zed token variables, disables interactive Git
authentication and system Git configuration, and enables local file transport
only through process-local Git configuration for its own fixtures.

No public registry, package namespace, account, token, container daemon, or
persistent runner state is used. Each operating-system job retains only the
bounded command transcript and JSON evidence containing the exact binary
version, binary SHA-256, and fourteen completed checks.

The workflow itself has `contents: read`, disables persisted checkout
credentials, and pins all third-party Actions and product inputs to full commit
SHAs.
