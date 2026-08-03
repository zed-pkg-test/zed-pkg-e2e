# Lifecycle fixture source-map contract

The stateless lifecycle harness must know where every declared fixture
dependency can be published from. `scripts/lifecycle.py` owns two disjoint
registries:

- `PACKAGE_SOURCES`: native Zed package name to fixture repository and manifest
  path; and
- `NON_PACKAGE_REPOS`: repositories that deliberately exercise the negative
  no-manifest contract.

A repository may not appear in both classifications. When a fixture is promoted
from a schema or support repository into a published Zed package, the source map
and non-package classification must change in the same reviewed commit.

## Shared-schema promotion

`zed-pkg-test/shared-schema` now publishes
`zedtest/shared-schema@1.0.0`. The source map therefore contains:

```python
"zedtest/shared-schema": ("shared-schema", ".")
```

and `shared-schema` is no longer a non-package repository. This allows
`python-app` and future heterogeneous consumers to seed the exact schema package
before their own lifecycle begins. It also makes the `shared-schema` matrix row
run release, deterministic pack, r2g, publish, copy install, frozen replay,
yank, and restore checks instead of expecting all package commands to fail.

## Source-cleanliness invariant

`zed publish` writes its deterministic archive to `.zed/pack/` even when a
fixture repository does not ignore that path. The lifecycle harness removes
only the exact untracked archives expected from the manifest outputs before it
asserts that the source checkout is clean.

This cleanup is deliberately narrow:

- the expected filename is derived from the exact package name and version;
- only `?? .zed/pack/<expected>.tar.gz` status entries may be removed;
- tracked or modified files are rejected;
- unexpected files are rejected;
- symlinks, directories, and non-regular files are rejected; and
- all other source mutations still fail the lifecycle job.

Repositories that already ignore `.zed/pack` produce no status entries and need
no cleanup. The harness does not treat arbitrary `.zed` content as disposable.

## Drift gate

`.github/workflows/lifecycle-source-map.yml` checks out immutable revisions of
`python-app`, `shared-schema`, and `polyglot-lib`. The standard-library contract
test proves that:

1. every dependency declared by the Python consumer has a source-map entry;
2. each mapped path contains a `.zpkg.toml`;
3. the manifest's published outputs include the dependency name being mapped;
4. package source repositories are disjoint from `NON_PACKAGE_REPOS`;
5. repository names, package names, and relative paths are normalized;
6. the exact transient publish archive can be removed without weakening source
   cleanliness;
7. unexpected or tracked pack files are never deleted; and
8. the checks leave all harness and fixture repositories clean.

The workflow has read-only permissions, bounded execution, immutable Action and
fixture pins, no Python bytecode side effects, and no registry or publication
credentials.
