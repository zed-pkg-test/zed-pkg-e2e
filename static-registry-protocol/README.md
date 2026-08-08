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

Org segments must already be lowercase canonical form. The generator also
requires a new or empty real output directory and rejects fixture/output
symlinks and special files. These rules prevent stale objects and filesystem
escape from entering an export before protocol trust is evaluated.

## Tools

- `build_static_registry.py --fixtures fixtures --out tree` — deterministic
  build: ustar, sorted entries, `SOURCE_DATE_EPOCH` (default 0), uid/gid 0,
  `zstd -19`. Two builds of the same fixtures are byte-identical.
- `check_static_registry.py --base <dir|https://host>` — conformance: discovery
  sanity, checkpoint↔object integrity, NDJSON/semver/cksum verification, yank
  semantics (unresolvable but bytes retained), absent-package hard miss. Local
  trees must contain exactly the checkpointed objects plus `checkpoint.json`;
  uncheckpointed files, symlinks, and special files are rejected.
- `check_static_registry.py --base <dir> self-test` — **red tests**: six
  mutations (tarball tamper, index tamper, checkpoint drop, yank resurrection,
  wrong schema, uncheckpointed object) must each turn the checker red. A green
  run is meaningful only because these prove red is reachable.
- `sync_to_r2.sh <tree> <bucket>` — verifies the local tree, uploads immutable
  `pkgs/**` with one-year immutable caching, uploads mutable v0 discovery/index
  objects with `no-cache`, and publishes `checkpoint.json` last. Replacing an
  existing v0 checkpoint fails closed unless an operator explicitly sets
  `ZPKG_ALLOW_V0_REPLACE=true` for a disposable non-production experiment.

Version 0 cannot make a rolling update collection-atomic because index paths are
mutable. Checkpoint-last ordering reduces exposure for an initial publication,
but v1 must move indexes under immutable snapshot prefixes selected by a signed
stable checkpoint.

## Permanent credential-free CI

`.github/workflows/static-registry-protocol.yml` runs the local contract on
Ubuntu 24.04 and macOS 15 with read-only repository permissions and immutable
Action pins. Each job:

1. verifies the pinned Python and runner-provided `zstd` boundary;
2. runs generator safety tests for empty outputs, stale outputs, symlinked
   fixtures/outputs, uppercase orgs, and empty fixture sets;
3. runs fake-AWS synchronization tests against the real generated fixture,
   including exact object order/cache policy, checkpoint-last publication,
   replacement refusal/override, and pre-AWS rejection of missing, tampered, or
   uncheckpointed state;
4. builds the registry twice in unrelated temporary directories with
   `SOURCE_DATE_EPOCH=0`;
5. compares the complete sorted path/size/SHA-256 inventory byte-for-byte;
6. runs the green conformance check;
7. runs all six red mutations and requires every one to fail;
8. proves the source checkout remained unchanged; and
9. uploads bounded commit-addressed local evidence for seven days.

The permanent workflow reads no GitHub, Cloudflare, R2, registry, signing, or
shared-auth secret. Live object-store synchronization remains a separate manual
and environment-gated operation; a pull request can certify the protocol
fixture and uploader contract without network access or infrastructure
authority.

## Live fixture (2026-08-08)

- Bucket: `zed-pkg-static-registry-e2e` (dedicated; do **not** reuse
  `zed-pkg-artifacts-e2e`, which belongs to the API-path R2 certification, and
  never the prod `zed-pkg-artifacts`).
- Public base: `https://pub-9b365e012501426c9de90d2746deac9d.r2.dev`
  (r2.dev is rate-limited and not a production surface; fine for e2e).
- `evidence/` holds the recorded local + live runs.

The recorded live fixture predates the stricter v0 replacement rule. Future
manual live experiments should use a fresh dedicated bucket or explicitly mark
the bucket disposable; the permanent CI path never mutates the live fixture.

## Operational findings worth carrying into the RFC (DEN-2854)

1. **r2.dev filters script User-Agents**: `Python-urllib/3.9` gets `403`;
   curl's UA and a custom `zpkg-static-registry-check/0` UA pass. Registry
   clients must always send an identifying UA; conformance tooling can't rely
   on language-default agents.
2. **Managed-domain enablement propagates asynchronously**: the first ~minutes
   after enabling the r2.dev domain returned intermittent `403`s. Tooling that
   provisions a static registry must retry/verify before declaring it live.
3. Per-object `Cache-Control` survives verbatim through R2. Immutable package
   bytes can use long caching, while mutable v0 pointers must revalidate until
   v1 snapshot paths make index objects immutable.
4. Deterministic `.tar.zst` needs all of: ustar format, sorted entries, zeroed
   mtime/uid/gid/uname, fixed mode, and a pinned zstd level; with those, the
   whole tree (including `tree_sha256`) is byte-reproducible.
