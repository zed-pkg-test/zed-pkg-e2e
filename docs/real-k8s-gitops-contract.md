# Real `k8s-cluster` GitOps contract certification

This test-org lane validates the actual merged ORESoftware GitOps contract rather than only a synthetic fixture.

## Immutable inputs

The default certification pins both repositories by full commit:

- `zed-pkg/zed-cli@ace157406d04f338698b6dc93a77021b07560e72`
- `ORESoftware/k8s-cluster@cfe39128e309936a4bcaeca4aaa35e4b9bec8888`

The Zed commit contains the standalone validator plus the reviewed root `zed gitops ...` dispatch candidate. The cluster commit contains the merged v1alpha1 catalog, exact-pin validator, deterministic preview renderer, and inert ApplicationSet pilot from DEN-2724.

## What the workflow proves

The workflow checks out both public repositories without persisted credentials or submodule materialization, verifies the exact commit identities, and then:

1. runs the focused Zed root-dispatch integration test;
2. runs the native `k8s-cluster` GitOps validator test suite;
3. builds exact release `zed` and `zed-gitops` siblings;
4. runs the native cluster validator and deterministic preview renderer against the real Git index and catalog;
5. runs both `zed-gitops validate` and root-dispatched `zed gitops validate` against that same checkout;
6. requires identical root/standalone reports and agreement with the native validator's record count;
7. verifies the real catalog retains exact gitlink-to-source pin parity and `pilot-inert` migration posture;
8. verifies the ApplicationSet template retains `missingkey=error`, exact child target revisions, collision-free names, and its explicit inert annotation; and
9. proves no other Argo YAML/JSON file references the pilot ApplicationSet by name or filename.

The resulting artifact uses schema:

```text
zed-pkg-test/real-k8s-gitops-contract/v1
```

It contains only the two immutable commits, report and preview SHA-256 values, record counts, and passed check names.

## Isolation and non-goals

The workflow has read-only repository permission. All Actions are pinned to immutable commits, all checkouts disable persisted credentials, and subprocesses remove credential-shaped environment variables.

It performs no submodule clone, private repository read, registry write, Kubernetes mutation, Argo CD reconciliation, Cloudflare request, DNS change, R2 access, or persistent namespace operation. Passing this contract does **not** authorize activation of the pilot ApplicationSet. Activation still requires a separate production PR with ownership parity, prune/deletion behavior, and rollback evidence.

## Coordination

- Cluster implementation: `ORESoftware/k8s-cluster#1109`
- Root dispatch: `zed-pkg/zed-cli#230`
- Three-platform dispatch canary: `zed-pkg-test/zed-pkg-e2e#118`
- GitOps roadmap: `ORESoftware/k8s-cluster#1097`
- Linear: `DEN-2724`, `DEN-2725`, and `DEN-630`
