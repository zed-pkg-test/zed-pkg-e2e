# static-registry-protocol — fixture + conformance harness (sketch v0)

Groundwork for the zpkg registry-protocol work in Linear project **zpkg
Self-Hosted Registries and Private Deps**: the protocol RFC (DEN-2854), the
static read-only export (DEN-2857), and the static arm of the e2e certification
(DEN-2861). GitHub anchor: `zed-pkg/zed-interfaces#45`.

This is **not** the final protocol. It exists to make the load-bearing design
constraint testable *now*:

> the public read path is plain files — an entire registry read side must be
> servable by dumb object/static hosting with no running daemon.

What v0 deliberately omits (owned by the DEN-2854 RFC): `registry_id` and trust
roots, signed checkpoints (the `checkpoint.json` here has a `signature: null`
slot), lifecycle states beyond `yanked`, and projection/target semantics.

## Layout produced

```
.well-known/zpkg-registry.json   discovery: schema_version, endpoints,
                                 auth_modes=[none], publish_supported=false
index/<org>/<name>               NDJSON; one line per version, ascending semver:
                                 {version, deps, cksum:"sha256:…", size, yanked}
pkgs/<org>/<name>/<ver>.tar.zst  content-addressed deterministic tarballs
checkpoint.json                  {seq, files:[{path,sha256,size}], tree_sha256,
                                 signature:null}
```

Org segments must already be lowercase canonical form (the generator hard-fails
otherwise — the wrong-org-segment bug class dies at build time).

## Tools

- `build_static_registry.py --fixtures fixtures --out tree` — deterministic
  build: ustar, sorted entries, `SOURCE_DATE_EPOCH` (default 0), uid/gid 0,
  `zstd -19`. Two builds of the same fixtures are byte-identical.
- `check_static_registry.py --base <dir|https://host>` — conformance: discovery
  sanity, checkpoint↔object integrity, NDJSON/semver/cksum verification, yank
  semantics (unresolvable but bytes retained), absent-package hard miss.
- `check_static_registry.py --base <dir> self-test` — **red tests**: five
  mutations (tarball tamper, index tamper, checkpoint drop, yank resurrection,
  wrong schema) must each turn the checker red. A green run is only meaningful
  because these prove red is reachable (same vacuity rule as DEN-2861).
- `sync_to_r2.sh <tree> <bucket>` — uploads with the protocol's cache
  semantics: `pkgs/**` → `public, max-age=31536000, immutable`;
  index/discovery/checkpoint → `public, max-age=60, stale-while-revalidate=600`.

## Live fixture (2026-08-08)

- Bucket: `zed-pkg-static-registry-e2e` (dedicated; do **not** reuse
  `zed-pkg-artifacts-e2e`, which belongs to the API-path R2 certification, and
  never the prod `zed-pkg-artifacts`).
- Public base: `https://pub-9b365e012501426c9de90d2746deac9d.r2.dev`
  (r2.dev is rate-limited and not a production surface; fine for e2e).
- `evidence/` holds the recorded local + live runs.

## Operational findings worth carrying into the RFC (DEN-2854)

1. **r2.dev filters script User-Agents**: `Python-urllib/3.9` gets `403`;
   curl's UA and a custom `zpkg-static-registry-check/0` UA pass. Registry
   clients must always send an identifying UA; conformance tooling can't rely
   on language-default agents.
2. **Managed-domain enablement propagates asynchronously**: the first ~minutes
   after enabling the r2.dev domain returned intermittent `403`s. Tooling that
   provisions a static registry must retry/verify before declaring it live.
3. Per-class `Cache-Control` survives verbatim through R2 (verified in response
   headers) — the immutable-pkgs / short-TTL-index split the caching plan
   assumes is enforceable from object metadata alone.
4. Deterministic `.tar.zst` needs all of: ustar format, sorted entries, zeroed
   mtime/uid/gid/uname, fixed mode, and a pinned zstd level; with those, the
   whole tree (including `tree_sha256`) is byte-reproducible.
