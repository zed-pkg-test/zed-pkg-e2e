# Exact direct-source snapshot Kustomize render certification

This `zed-pkg-test` lane proves that the submodule-backed inventory record and the Argo CD direct-source declaration resolve to the same exact private child commit, and that checksum-pinned manifest bytes from that commit render deterministically without materializing the submodule in the cluster superproject.

## Private-repository boundary discovered by the first run

The first Windows job attempted a credential-free cross-organization checkout of `daedalus-fab/fabrication-server.rs` and failed with `Repository not found`. That is the expected GitHub permission boundary: a test-org `GITHUB_TOKEN` is scoped to the test repository and cannot read a private repository in another organization.

The workflow does **not** inject a PAT or broaden its token. Instead, the five deployment files were exported through the connected repository reader and committed as an immutable test fixture. Every snapshot file retains the exact Git blob SHA from the source commit, and every runner recomputes `git hash-object` before rendering. The test therefore proves source-byte provenance and renderability without claiming private-repository reachability.

Private source reachability remains a separate, owner-scoped GitHub App credential contract.

## Immutable inputs

```text
ORESoftware/k8s-cluster@4bc664a651cd912e8c16a15dd8f0dfcb1e1870b5
daedalus-fab/fabrication-server.rs@5e305b9bf40d28139bdabf9342c304f759cbfee5
Kustomize v5.8.1
```

The cluster commit is the repair proposed in `ORESoftware/k8s-cluster#1209`. It aligns the catalog inventory and direct Argo source with the indexed child gitlink and adds deployment-gitlink paths to the GitOps validator trigger.

## Three-platform proof

Ubuntu 24.04, macOS 15, and Windows Server 2025 independently:

1. check out the exact cluster commit with `submodules: false`;
2. load the five-file source snapshot and its revision/repository/blob manifest;
3. recompute every snapshot file’s Git blob SHA and require an exact match with the private source commit;
4. verify `.gitmodules`, the mode-160000 Git index entry, catalog inventory, catalog direct source, and snapshot metadata identify the same repository and commit;
5. prove the deployment source is not materialized beneath the superproject checkout;
6. download the official Kustomize v5.8.1 asset selected for the runner platform;
7. verify the release asset against a hard-coded official SHA-256 digest before safe extraction;
8. render the checksum-pinned `k8s` snapshot twice and require byte-identical output;
9. require exactly one `Deployment`, `Service`, `NetworkPolicy`, and `ExternalSecret` in namespace `daedalus`;
10. reject cluster-scoped resources, a rendered Kubernetes `Secret`, or plaintext `stringData`; and
11. upload the rendered YAML plus source-blob, binary, and render digests.

The pinned release assets and digests come from the official `kubernetes-sigs/kustomize` v5.8.1 GitHub release. Archive extraction rejects path traversal and links.

## Why this is separate from the catalog validator

The GitOps catalog validator intentionally needs no child worktree or network access. It proves declaration and gitlink integrity from the superproject alone. This canary adds a source-byte integration boundary: the exported files are tied to the exact private source commit by their Git blob identities, then rendered with a checksum-pinned toolchain.

The layers remain distinct:

- static catalog validation must work during a private-repository credential outage;
- snapshot rendering proves the exact exported deployment bytes still render;
- an authenticated GitHub App checkout must separately prove live private-repository reachability.

## Isolation

All repository checkouts disable persisted credentials. The canary uses no user PAT, GitHub App private key, registry credential, Kubernetes credential, Cloudflare token, DNS permission, R2 key, or database access. It runs `kustomize build` only; it never invokes `kubectl`, `argocd`, Helm installation, a registry push, or an apply operation.

Passing this canary does not activate the inert ApplicationSet and does not authorize production reconciliation.

## Coordination

- Catalog repair: `ORESoftware/k8s-cluster#1209`
- Current-main adversarial matrix: `zed-pkg-test/security-adversarial-e2e#6`
- Root dispatcher merge: `zed-pkg/zed-cli#242`
- GitOps roadmap: `ORESoftware/k8s-cluster#1097`
- Linear: `DEN-2724`, `DEN-2725`, `DEN-630`
