# Public GitHub and Cloudflare fallback attestation

This canary is deliberately read-only. It uses immutable public release assets
in `zed-pkg-test/github-api-fallback-canary` and never creates a repository,
tag, release, object, account, R2 object, DNS record, or Cloudflare Worker.

The edge matrix proves all four current release fixtures independently and
without a GitHub credential:

- direct anonymous GitHub repository and Release API visibility;
- sidecar, compressed byte count, and SHA-256 agreement;
- byte-identical delivery through `cdn.zpkg.net`;
- Cloudflare/Zed provenance headers and immutable caching;
- `HEAD` behavior and bounded range behavior;
- missing and malformed paths fail closed; and
- the `zpkg.net` public site remains independently hosted by GitHub Pages.

GitHub redirects release downloads through short-lived signed object URLs. The
attestation strips every query string and fragment before writing logs or the
retained JSON artifact, so only the public hostname and path are preserved.

`registry.zpkg.net` is always observed. It becomes a required check only when
`PUBLIC_REGISTRY_FALLBACK_ENFORCE=true` or a manual dispatch selects
`enforce_live_registry`. Until the reviewed registry Worker is deployed, the
attestation retains the live 502 as evidence rather than turning it into a
false green.

The second job builds an immutable `zed-pkg/zed-cli` commit and points it at an
unreachable loopback registry. It requires both the initial install and a wiped,
frozen reinstall to restore the public GitHub Release payload without changing
the lockfile. Release sidecars and archive bytes remain publicly readable. The
CLI's package-resolution phase additionally reads the GitHub tags API; that
request receives only the job-scoped `contents:read` Actions token so shared
GitHub-hosted runner IPs do not make the test intermittent by exhausting the
anonymous API quota. The token value is never printed or retained.
