# Git-submodule package-boundary canary

This contract independently certifies the archive and publication boundary in
`zed-pkg/zed-cli#110`. It invokes only the compiled public executable and
inspects the resulting `tar.gz` bytes with Python's standard library.

The workflow pins exact CLI candidate
`08827a11147355cdeedb68cedcc05c1104265c1f` and exact interface contract
`c2e049006453c26ca8ca291783f681fce75cb01f`. Ubuntu 24.04 and macOS 15 build
that source, run the focused product tests, and execute disposable local Git
fixtures. Linux additionally runs all-target Clippy with warnings denied.

## Certified behavior

The fixture graph is a superproject containing a Zed-package submodule, which
itself contains a nested ordinary Git submodule. The black-box contract proves:

1. an included but uninitialized submodule rejects `zed pack` before an archive
   is created;
2. the same checkout rejects `zed publish --dry-run` before publication
   planning;
3. `zed install --git-submodules` initializes both levels recursively;
4. a materialized checkout packages root, child, and nested runtime files;
5. no root or nested `.git`, `.gitmodules`, `.hg`, or `.svn` control data enters
   the archive;
6. the initialized package reaches a credential-free publication dry run;
7. a dirty included submodule fails before archive creation;
8. a dirty nested submodule also fails before archive creation;
9. an authored `publish.exclude = ["vendor/client/**"]` permits that entire
   subtree to remain uninitialized and omitted; and
10. a project `.zedignore` with the same recursive boundary behaves identically.

An uninitialized submodule is optional only when one authored recursive rule
conclusively excludes its complete subtree. Sampling a few likely filenames is
not treated as proof that an unknown runtime file cannot enter the package.

## Isolation and evidence

Every Git repository, Zed home, registry directory, output directory, and
credential home is created below the runner's temporary work root. The contract
removes inherited GitHub and Zed token variables, disables interactive Git
authentication and system Git configuration, and enables local file transport
only through process-local Git configuration for its own fixtures.

No public registry, package namespace, account, token, container daemon, or
persistent runner state is used. Each operating-system job retains only the
bounded command transcript and JSON evidence containing the exact binary
version, binary SHA-256, and ten completed checks.

The workflow itself has `contents: read`, disables persisted checkout
credentials, and pins all third-party Actions and product inputs to full commit
SHAs.
