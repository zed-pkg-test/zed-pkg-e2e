# GitHub dependency-inventory conformance (DEN-2957)

This directory certifies the repository and GitHub-organization ingestion behavior planned for `zed graph github` before the production Rust CLI adopts it.

## Resource boundary

The emitted resource is `zpkg/github-dependency-inventory/v1`. It is a source inventory pinned to exact repository commits. It is **not** a universal resolved package graph and always reports:

```json
{
  "completeness": {
    "inventory": "complete",
    "resolution": "not-claimed"
  }
}
```

A `.zpkg.lock` contributes exact package pins, while `.zpkg.toml`, `.gitmodules`, `flake.nix`, `flake.lock`, and governed Nix `sources.json` files contribute typed source edges. Only DEN-2865's shared resolver builder may claim an exact `zpkg/dependency-graph/v1` resolution.

## Reference command

The dependency-free test-org reference implementation is split by trust boundary under `harness/github_inventory_*.py`; the CLI facade mirrors the intended production surface:

```console
python harness/github_dependency_inventory.py \
  --org zed-pkg \
  --repo zed-pkg/zed-cli \
  --include zed,git-submodule,nix \
  --format json \
  --output inventory.json
```

`--format` also accepts `dot` and `mermaid`. Repeated `--repo`, `--org`, and `--include` values are normalized, deduplicated, and sorted. Credentials are read only from `ZED_PKG_GITHUB_TOKEN` or `GITHUB_TOKEN`; there is intentionally no token flag. A token is sent automatically only to `api.github.com` or a loopback fake server. GitHub Enterprise or another custom HTTPS origin additionally requires `ZED_PKG_GITHUB_ALLOW_TOKEN_TO_API_BASE=1`, preventing an API-base override from silently exfiltrating the default GitHub token.

Offline certification uses the compact fixture API:

```console
python harness/github_dependency_inventory.py \
  --fixture fixtures/github-dependency-inventory/fixture.json \
  --org acme \
  --repo acme/app \
  --include zed,git-submodule,nix \
  --format json
```

## Deterministic envelope

The canonical JSON envelope records:

- normalized repository and organization inputs;
- every discovered repository, including archived repositories and explicit acquisition failures;
- exact default-branch commit SHA before tree or blob acquisition;
- source manifests with exact blob SHA and byte count;
- repository/package nodes and typed edges;
- provenance containing repository commit, manifest kind, path, blob SHA, and line or JSON pointer where available;
- SCCs, canonical cycle sets, and dependency-first waves over the SCC condensation graph;
- contradiction diagnostics such as conflicting lock pins, missing gitlinks, and unmapped gitlinks;
- hard configured limits and bounded request/byte usage.

The JSON writer sorts keys and all semantically unordered collections. DOT and Mermaid use hash-derived identifiers and format-specific label escaping. Golden outputs are regenerated independently on Linux, macOS, and Windows and compared byte-for-byte.

## Hardening invariants

1. Organization pagination follows only same-origin `rel="next"` links and rejects pagination cycles.
2. HTTP redirects are not followed, preventing authorization forwarding to another origin.
3. Plain HTTP API origins are accepted only on loopback for the fake-server suite.
4. Retry count and `Retry-After` sleep are bounded. Response bodies from failed HTTP requests are never retained or surfaced.
5. Repository, node, edge, request, per-response byte, total-response byte, manifest byte, individual field byte, repository-tree entry, JSON-depth, and wall-clock limits fail closed. Successful HTTP bodies are bounded while they are read, not after unbounded buffering. A limit error never becomes a partial inventory.
6. GitHub recursive-tree truncation is an explicit repository failure; it is never interpreted as a complete scan.
7. Blob content is fetched by exact tree SHA, size-checked, bounded, and decoded from GitHub's declared base64 representation.
8. Output replacement uses a same-directory temporary file, file `fsync`, atomic replacement, and directory `fsync` where supported. Failed runs preserve the prior output and remove temporary files.
9. Tokens are absent from argv, output, filenames, request paths, retained errors, graph labels, and golden evidence.
10. Inventory failures may produce a versioned partial artifact and exit status `1`; invalid input exits `2`; hard limits exit `3`. A complete artifact exits `0`.

## Fixture coverage

The checked-in fixture, split semantic/transport suites, and local fake server cover:

- multi-page organization discovery and duplicate input normalization;
- a private repository and bearer-authenticated requests;
- archived and manifest-free repositories;
- Zed declaration diamonds and an explicit cycle;
- lock pins, Git submodule gitlinks, flake declarations/locks, and Nix source pins;
- hostile requirement text that attempts DOT/Mermaid label injection;
- HTTP 403/429/5xx rate-limit retry behavior, redirects, truncated trees, bounded response reads, byte/field/depth limits, wrapped base64 blobs, custom-origin token policy, and token-bearing error bodies;
- failure-atomic output, a 10,000-node non-recursive SCC stress graph, and byte-identical JSON/DOT/Mermaid goldens.

## Production handoff

Once this test-org PR is green, the production `zed-pkg/zed-cli` integration should add the modular `zed graph github` command and port this behavior without editing the active DEN-2864 contract branch (`zed-pkg/zed-interfaces#52`) or introducing a second package resolver. The test fixture and goldens remain the cross-language conformance authority until the shared Rust builder supersedes them explicitly.
