# Cross-organization Windows `zed develop` canary

This repository is the fixture-backed release acceptance boundary for Zed. The
workflow `.github/workflows/windows-develop-clean-room.yml` independently runs
the Windows shell contract owned by `zed-pkg/zed-e2e` against one immutable
merged `zed-cli` commit.

## Why this is separate

`zed-pkg/zed-cli` owns implementation tests. `zed-pkg/zed-e2e` owns the primary
external clean-room contract. This repository adds a third release perspective:
it checks out that exact external contract from another organization and proves
it can build and exercise the merged CLI without branch-local helpers, private
credentials, a registry service, or state retained from an earlier job.

The canary is additive. It does not replace the primary E2E workflow or permit a
failed primary check to be ignored.

## Immutable inputs

```text
zed-e2e        144d17e28afa9586bc71ec43f340a0e0328a54b4
zed-cli        fd3b3e487b2bdd129dd67403ad51f7299cfe6828
zed-interfaces c2e049006453c26ca8ca291783f681fce75cb01f
flags-2-env    2f62e40932a0fcb8b9bf1b4c84473e34fa3c51c7
```

Every source and third-party Action is pinned to a full commit. Pin changes are
reviewed changes and require a complete replay.

## Contract

On `windows-2022`, the canary builds the real `zed.exe` and imports the exact
policy and functional harness from the pinned E2E commit. It verifies:

- canonical `develop` / `dev` equivalence;
- PowerShell profile suppression, selected project cwd, and child exit
  propagation;
- cmd.exe `/D /S /C`, selected cwd, managed environment, and `ERRORLEVEL`;
- default `COMSPEC` behavior;
- project-local Python virtual environments through `Scripts` and `sys.prefix`;
- isolated HOME and USERPROFILE without credential copying;
- no implicit dotenv, PowerShell profile, registry-token, or provider-secret
  loading;
- bounded `--no-install` writes;
- actionable failure diagnostics;
- read-only workflow permissions and immutable source pins; and
- clean source checkouts without reset or cleanup-based masking.

The cmd.exe fixture discovers the nearest manifest-owning project root and
creates two fixed relative batch names there. One batch performs native
statements; the launcher captures and returns its `ERRORLEVEL`. Zed executes the
launcher through relative `CALL`, avoiding an incidental absolute-path quoting
contract while preserving the product behavior under test.

## Evidence

The workflow stores only the bounded environment identity file and the
machine-readable clean-room report for seven days. It scans every retained file
for all fake credential canaries before upload. Temporary homes, virtual
environments, source checkouts, and batch fixtures are not retained.

The owning Linear issues are DEN-1614, DEN-1616, and DEN-1634. The implementation
merged through `zed-pkg/zed-cli#100`; the primary external contract is
`zed-pkg/zed-e2e#16`.
