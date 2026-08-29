# Live GHCR fallback canary

This proves GitHub Packages (GHCR) can host a packed Zed artifact when
`registry.zpkg.net` is down. Unlike the Releases canary
(`docs/github-api-fallback-canary.md`) this fixture has **no GitHub Release**.
`zed install` source fallback tries a Release sidecar first, then GHCR; a
package that exists only as `ghcr.io/zed-pkg-test/ghcr-fallback-canary:v0.0.1`
forces the Packages branch.

The fixture repository is
[`zed-pkg-test/ghcr-fallback-canary`](https://github.com/zed-pkg-test/ghcr-fallback-canary)
(created by the script if missing). The OCI artifact uses Zed media types
(`application/vnd.zed.package.v1.tar+gzip` layer).

## What is asserted

1. GHCR HTTP: blob upload, manifest PUT for tag `v0.0.1`, GET of the same
   digest.
2. Optional: `zed install` / `zed install --frozen` against a **closed**
   `127.0.0.1` registry restores the payload from GHCR. Needs a zed-cli build
   with `source_fallback::ghcr_version` (zed-pkg/zed-cli#298).

Token needs `write:packages` (and `repo` to create the public fixture). Read
from `GITHUB_TOKEN` / `GH_TOKEN` / `ZED_PKG_GITHUB_TOKEN`; never printed.

## Local run

```bash
python3 scripts/ghcr_fallback.py --work-root /tmp/zed-ghcr-contract --skip-network

python3 scripts/ghcr_fallback.py --work-root /tmp/zed-ghcr-live

python3 scripts/ghcr_fallback.py \
  --work-root /tmp/zed-ghcr-zed \
  --zed /path/to/zed
```
