# `zed graph github` production certification matrix

This test-org workflow certifies an exact `zed-pkg/zed-cli` branch, tag, or commit without consuming production-org Actions minutes.

## Dispatch

Run **zed-cli graph github production** with an exact candidate ref. Prefer a full 40-character commit SHA for promotion evidence.

- `zed_cli_ref`: exact `zed-pkg/zed-cli` candidate ref.
- `run_live_smoke`: optional. The live `zed-pkg-test` organization scan runs only on Linux; macOS and Windows record an intentional skip while still running the deterministic fake-server suite.

No GitHub token may be passed in command arguments. The candidate reads `ZED_PKG_GITHUB_TOKEN` from the job environment. The workflow does not persist checkout credentials.

## Required matrix

Every candidate must pass on Linux, macOS, and Windows:

1. locked full-target/full-feature Cargo tests and build;
2. `zed graph github --help` contract, including repository/org inputs, source filters, JSON/DOT/Mermaid output, output path, and loopback/GitHub Enterprise API-base support;
3. absence of token/password/secret command-line options;
4. repeated deterministic fake GitHub scans with duplicate/mixed-case repository, organization, and include inputs;
5. byte-identical JSON, DOT, and Mermaid outputs;
6. exact default-branch commit, tree, blob, and gitlink evidence;
7. same-origin pagination acceptance and cross-origin pagination rejection;
8. authorization-bearing redirect rejection;
9. explicit partial inventory for a truncated recursive tree;
10. non-topological flat lock pins, with a transitive pin unable to fabricate a package edge;
11. iterative SCC/cycle/wave results over proven topology only;
12. renderer escaping for hostile requirements;
13. credential absence from stdout, stderr, graph output, request URLs, and bounded evidence.

The fake server is loopback-only and requires the sentinel token on every request. It emits two same-origin organization pages, archived/private repository metadata, exact commit/tree/blob responses, Zed declarations and lock pins, a validated gitlink, Nix inputs, a two-node cycle, a cross-origin pagination trap, a redirect trap, and a truncated-tree partial case.

## Live test-org smoke

The optional Linux live smoke scans `zed-pkg-test` twice for each format and compares bytes. Exit `0` means complete inventory; exit `1` is accepted only when the artifact explicitly reports partial inventory. Evidence stores hashes, byte counts, immutable candidate identity, and aggregate counts—not raw inventories, response bodies, or credentials.

A changing test-org head can make repeated live bytes differ. In that case, rerun after the test repositories are quiescent or add an exact-head fixture; do not weaken deterministic fake-server checks.

## Promotion rule

A production PR may reference this workflow only after the run is tied to the PR's exact head SHA. Branch-name-only runs are exploratory and cannot authorize merge. The production PR must also retain `resolution=not-claimed`; exact package resolution remains owned by DEN-2865.

The durable AI review queue is `ORESoftware/ai-agent-bridge.rs#104` / DEN-2871. Queueing a review does not count as approval while the provider bridge is unavailable.