# Exact direct-source Kustomize render certification

This `zed-pkg-test` lane proves that the submodule-backed inventory record and the Argo CD direct-source declaration resolve to the same exact public child commit, and that the child deployment source renders deterministically without materializing the submodule in the cluster superproject.

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
2. check out the exact child repository separately;
3. verify `.gitmodules`, the mode-160000 Git index entry, catalog inventory, catalog direct source, and child checkout all identify the same repository and commit;
4. prove the deployment source is not materialized beneath the superproject checkout;
5. download the official Kustomize v5.8.1 asset selected for the runner platform;
6. verify the release asset against a hard-coded SHA-256 digest before extraction;
7. render the exact child `k8s` directory twice and require byte-identical output;
8. require exactly one `Deployment`, `Service`, `NetworkPolicy`, and `ExternalSecret` in namespace `daedalus`;
9. reject cluster-scoped resources, a rendered Kubernetes `Secret`, or plaintext `stringData`; and
10. upload the rendered YAML plus provenance, binary, and render digests.

The pinned release assets and digests are taken from the official `kubernetes-sigs/kustomize` v5.8.1 GitHub release. Archive extraction rejects path traversal and links.

## Why this is separate from the catalog validator

The GitOps catalog validator intentionally needs no child worktree or network access. It proves declaration and gitlink integrity from the superproject alone. This canary adds a separate, public-source integration boundary: it fetches the exact declared child repository and proves that its declared renderer and path produce deterministic, namespace-scoped manifests.

The two layers should remain distinct. A private-repository credential outage must not prevent static catalog validation, while a passing static catalog check must not be treated as proof that the declared child source still renders.

## Isolation

All repository checkouts disable persisted credentials. The canary uses no user PAT, GitHub App private key, registry credential, Kubernetes credential, Cloudflare token, DNS permission, R2 key, or database access. It runs `kustomize build` only; it never invokes `kubectl`, `argocd`, Helm installation, a registry push, or an apply operation.

Passing this canary does not activate the inert ApplicationSet and does not authorize production reconciliation.

## Coordination

- Catalog repair: `ORESoftware/k8s-cluster#1209`
- Current-main adversarial matrix: `zed-pkg-test/security-adversarial-e2e#6`
- Root dispatcher merge: `zed-pkg/zed-cli#242`
- GitOps roadmap: `ORESoftware/k8s-cluster#1097`
- Linear: `DEN-2724`, `DEN-2725`, `DEN-630`
