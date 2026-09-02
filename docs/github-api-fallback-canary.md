# Live GitHub and Cloudflare fallback canary

The fallback program has two complementary harnesses and one explicit GitHub
Actions ownership boundary.

## Product-path canary

`scripts/zed_github_cloudflare_e2e.py` runs the released product path rather
than manufacturing Release assets itself. It requires the workflow in
[`zed-pkg-test/github-api-fallback-canary`](https://github.com/zed-pkg-test/github-api-fallback-canary)
because the repository-scoped `GITHUB_TOKEN` may write Releases only in the
repository that owns the workflow.

The workflow builds an exact `zed-pkg/zed-cli` commit and deliberately points
its registry write at an unreachable non-loopback host. A passing run proves:

1. `zed publish` falls back to a GitHub Release and uploads both the packed
   artifact and its `VersionMetadata` sidecar.
2. The unauthenticated GitHub Release URL returns bytes whose SHA-256 and size
   match the sidecar.
3. `cdn.zpkg.net/github/{owner}/{repo}/{tag}/{asset}` returns byte-identical
   content with `x-zed-edge: cdn`, `x-zed-source: github-release`, and immutable
   caching.
4. `registry.zpkg.net/v1/packages/{org}/{name}/versions/{version}` reconstructs
   the same metadata from the public sidecar with
   `x-zed-source: github-public`.
5. With both the configured registry and R2 source unavailable, `zed install`
   and `zed install --frozen` restore the payload directly from GitHub and keep
   the lockfile unchanged.

The canary uses a public repository and a non-secret payload. Tokens are read
from `GITHUB_TOKEN`, `GH_TOKEN`, or `ZED_PKG_GITHUB_TOKEN` and are never
printed or written into package metadata.

## API-helper canary

`scripts/github_api_fallback.py` remains a lower-level GitHub REST contract.
It creates deterministic test bytes with Python, publishes them through GitHub
REST, then optionally asks `zed install` to consume them. This is useful for
separating a GitHub API regression from a `zed publish` regression, but it is
not a substitute for the product-path canary above.

## Credential-free checks

The `zed-pkg-e2e` pull-request workflow performs only syntax and local naming
contracts. It intentionally does not attempt a sibling-repository write with
its own scoped token.

```bash
python3 -m py_compile \
  scripts/github_api_fallback.py \
  scripts/zed_github_cloudflare_e2e.py

python3 scripts/github_api_fallback.py \
  --work-root /tmp/zed-gh-api-contract \
  --skip-network

python3 scripts/zed_github_cloudflare_e2e.py \
  --work-root /tmp/zed-gh-cloudflare-contract \
  --skip-network
```

For a live local run, use a token authorized for the canary repository and an
exact `zed` binary under test:

```bash
python3 scripts/zed_github_cloudflare_e2e.py \
  --zed /absolute/path/to/zed \
  --package-root /absolute/path/to/github-api-fallback-canary \
  --work-root /tmp/zed-gh-cloudflare-live
```

Do not place credentials, private repository data, or user content in the
public Release assets or sidecars.
