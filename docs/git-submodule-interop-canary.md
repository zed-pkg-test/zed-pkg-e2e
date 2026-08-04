# Git-submodule interoperability canary

This repository independently certifies the Git-submodule surface shipped by
`zed-pkg/zed-cli` rather than relying only on the CLI repository's unit and
integration tests.

The workflow pins the exact merged CLI commit
`ef4e461a57203016ae9ac4d1d38849e7e8d508e0` and its exact interface contract
`c2e049006453c26ca8ca291783f681fce75cb01f`. It builds that source on Ubuntu
24.04 and macOS 15, then creates every Git repository and package registry below
the runner's temporary directory. It uses no account, token, public registry,
or persistent package namespace.

## Contract

`scripts/git_submodule_interop.py` verifies eight black-box behaviors:

1. `--git-submodules=false` overrides an inherited
   `ZED_PKG_GIT_SUBMODULES=1` setting.
2. `zed install --git-submodules` initializes top-level and nested submodules
   recursively from a fresh clone.
3. `zed overtake --git-submodules` imports the package as an exact workspace
   member and direct dependency and records immutable `[[git-submodule]]`
   provenance.
4. A second fresh clone can run `zed install --git-submodules --frozen`
   without changing one lockfile byte.
5. Dirty submodule content is rejected without lockfile mutation.
6. Committed `.gitmodules` branch drift is rejected without lockfile mutation.
7. A pre-commit resolution failure restores the exact authored root-manifest
   bytes and leaves no lock, modules tree, or transaction staging state.
8. The same failure removes a takeover-generated manifest when the project had
   no manifest before migration.

The fixture graph contains a nested local-path submodule, so recursion is tested
with real Git commands rather than mocked process output. Local file transport is
enabled only through the acceptance process's explicit Git configuration
environment; interactive prompting and package-registry credentials are disabled.

## Evidence

Each operating-system job retains:

- the complete command transcript;
- the adopted lockfile;
- the release binary SHA-256;
- the exact CLI version; and
- the list of certified contract checks.

The workflow has only `contents: read`, disables persisted checkout credentials,
and pins every third-party Action to a full commit SHA. The repository's existing
full lifecycle workflow also consumes `.zed-cli-ref`, so advancing that file in
the same pull request runs the exact merged CLI across all 22 fixture repositories.
