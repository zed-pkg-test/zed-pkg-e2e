# Cloudflare R2-first and GitHub-fallback canary

This repository certifies the public artifact retrieval order used by Zed:

1. the owned registry and API servers are authoritative;
2. immutable packed artifacts are read from the owned Cloudflare R2 bucket through
   `cdn.zpkg.net/artifacts/{sha256}.{ext}`;
3. only after that R2 object is unavailable may the Cloudflare CDN Worker proxy an
   anonymously readable GitHub Release through `cdn.zpkg.net/github/{owner}/{repo}/{tag}/{filename}`;
4. direct GitHub Release, GitHub Packages, and source-archive locators remain later
   client fallbacks.

The `/github/*` route is a Cloudflare proxy contract, not a public read oracle for
private R2 coordinate aliases. Only content-addressed `artifacts/<sha256>` objects
are read directly from the private bucket. This keeps public exposure bounded by an
immutable digest while still allowing Cloudflare to serve GitHub bytes when the
owned registry and R2 copy are unavailable.

## Exact Worker contract

`scripts/cloudflare_github_fallback_contract.mjs` imports the real
`zed-pkg/zed-infra` CDN Worker at an immutable commit and executes it with fake R2
and network adapters. It proves:

- an R2 hit returns `x-zed-source: r2`, exact bytes, and performs zero upstream
  requests;
- an R2 miss on a digest path does not guess a GitHub identity;
- the subsequent `/github/*` request follows only the allowlisted GitHub release
  redirect chain and returns `x-zed-source: github-release`;
- caller authorization and cookie headers are never forwarded to GitHub;
- the Worker streams the result from the Cloudflare hostname instead of redirecting
  the Zed client to GitHub.

## Black-box CLI canary

`scripts/github_r2_fallback.py` publishes a disposable package through a `file://`
registry, creates a frozen lock, and performs three clean installs while recording
the actual HTTP paths requested by the CLI:

1. a healthy loopback stand-in for the owned registry serves
   `/v1/artifacts/{sha256}`; the canary requires that path first and requires zero
   CDN requests;
2. with the registry unavailable and `artifacts/{sha256}.tar.gz` present in the CDN
   stand-in, the content-addressed R2 path must be first and `/github/*` must not be
   touched;
3. after removing the digest object and using a fresh Zed home, the observed CDN
   request prefix must be `artifacts/{sha256}.tar.gz` followed by
   `github/{owner}/{repo}/{tag}/{filename}`.

All three installs verify the packed payload, and the artifact digest is checked
against the frozen metadata. The canary also round-trips the `get_version` JSON
call/receipt frame over TCP NDJSON using the same key as
`zed-interfaces/route-maps/zed-api.route-map.json`, and compares generated
TypeScript route contracts between `zed-interfaces` and `zed-clients` when those
files are supplied.

## Local checks

From a sibling checkout containing `zed-pkg/zed-infra` and `zed-pkg/zed-cli`:

```bash
node scripts/cloudflare_github_fallback_contract.mjs \
  --infra-root ../zed-pkg/zed-infra

cargo build --release --manifest-path ../zed-pkg/zed-cli/Cargo.toml --bin zed
python3 scripts/github_r2_fallback.py \
  --zed ../zed-pkg/zed-cli/target/release/zed \
  --work-root /tmp/zed-github-r2-fallback \
  --route-map ../zed-pkg/zed-interfaces/route-maps/zed-api.route-map.json \
  --interfaces-generated-ts ../zed-pkg/zed-interfaces/generated/typescript/zed_api.ts \
  --clients-generated-ts ../zed-pkg/zed-clients/clients/typescript/src/generated/zed-api.routes.ts
```

The work root must not already exist. Production does not enable the loopback-only
fallback escape hatch used by the hermetic CLI test.
