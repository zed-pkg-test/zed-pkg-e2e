# Live GitHub API fallback canary

This proves GitHub can host a packed Zed artifact **without** `registry.zpkg.net`
or `cdn.zpkg.net`. It talks to `api.github.com` and `uploads.github.com` in
the sibling test org [`zed-pkg-test`](https://github.com/zed-pkg-test), then
downloads the same bytes from the public Release URL.

The fixture repository is
[`zed-pkg-test/github-api-fallback-canary`](https://github.com/zed-pkg-test/github-api-fallback-canary)
(created by the script if missing; always public so unauthenticated `GET`
works when the registry is down).

## What is asserted

1. GitHub REST: repo, git ref, tags, Releases, asset upload, asset download.
2. Unauthenticated `https://github.com/{org}/{repo}/releases/download/{tag}/{asset}`
   returns the exact sha256 that was uploaded.
3. Optional: `zed install` / `zed install --frozen` against a **closed**
   `127.0.0.1` registry, with `ZED_PKG_SOURCE_FALLBACK_ALLOW_LOOPBACK=true`,
   restores the payload from GitHub. That needs a zed-cli build that contains
   `source_fallback.rs` (zed-pkg/zed-cli#294).

This is complementary to the loopback CDN mock (`scripts/github_r2_fallback.py`
on `feat/github-r2-fallback-canary`): that test never leaves 127.0.0.1. This
one is the GitHub-API proof.

## Local run

```bash
# Contract only (no GitHub):
python3 scripts/github_api_fallback.py --work-root /tmp/zed-gh-api-contract --skip-network

# Live GitHub APIs (creates/updates the public canary repo + release):
python3 scripts/github_api_fallback.py --work-root /tmp/zed-gh-api-live

# Plus zed install through GitHub fallback:
cargo build --release --manifest-path ../zed-pkg/zed-cli/Cargo.toml --bin zed
python3 scripts/github_api_fallback.py \
  --work-root /tmp/zed-gh-api-zed \
  --zed ../zed-pkg/zed-cli/target/release/zed
```

The token is read from `GITHUB_TOKEN` / `GH_TOKEN` / `ZED_PKG_GITHUB_TOKEN`
and is never printed. The canary repo is public; do not put secrets in the
sidecar JSON.
