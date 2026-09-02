# Public GitHub and Cloudflare fallback attestation

This canary is deliberately read-only. It uses immutable public release assets
in `zed-pkg-test/github-api-fallback-canary` and never creates a repository,
tag, release, object, token, account, or Cloudflare resource.

The edge matrix proves two release sizes independently:

- direct anonymous GitHub repository and Release API visibility;
- sidecar, compressed byte count, and SHA-256 agreement;
- byte-identical delivery through `cdn.zpkg.net`;
- Cloudflare/Zed provenance headers and immutable caching;
- `HEAD` behavior and bounded range behavior;
- missing and malformed paths fail closed; and
- the `zpkg.net` public site remains independently hosted by GitHub Pages.

`registry.zpkg.net` is always observed. It becomes a required check only when
`PUBLIC_REGISTRY_FALLBACK_ENFORCE=true` or a manual dispatch selects
`enforce_live_registry`. Until the reviewed registry Worker is deployed, the
attestation retains the live 502 as evidence rather than turning it into a
false green.

The second job builds an immutable `zed-pkg/zed-cli` commit and points it at an
unreachable loopback registry. It requires both the initial install and a wiped,
frozen reinstall to restore the public GitHub Release payload without changing
the lockfile.
