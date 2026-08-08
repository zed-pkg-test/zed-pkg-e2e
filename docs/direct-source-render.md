# Exact direct-source snapshot Kustomize render certification

This `zed-pkg-test` lane proves that the submodule-backed inventory record and the Argo CD direct-source declaration resolve to the same exact private child commit, that the declared endpoint ports are permitted by the app NetworkPolicy, and that checksum-pinned manifest bytes from that commit render deterministically without materializing the submodule in the cluster superproject.

## Private-repository and billing boundaries

A credential-free cross-organization checkout of `daedalus-fab/fabrication-server.rs` failed with `Repository not found`. That is the expected GitHub permission boundary: a test-repository `GITHUB_TOKEN` cannot read a private repository in another organization.

The owner repository's Actions are also currently blocked before job execution by its billing/spending-limit state. Neither limitation is bypassed with a user PAT.

Instead, the five deployment files were exported through the connected repository reader and committed as an immutable test fixture. Every snapshot file retains the exact Git blob SHA from the source commit, and every runner recomputes `git hash-object` before rendering. This proves source-byte provenance and renderability without claiming private-repository reachability.

Private source reachability remains a separate, owner-scoped GitHub App credential contract.

## Immutable inputs

```text
ORESoftware/k8s-cluster@0c71eed05f8be50ac22ebe73715ded4a871234a8
daedalus-fab/fabrication-server.rs@cea4a3c772012e1f2f87050bac24e911b8e2e577
Kustomize v5.8.1
```

The source commit is owner PR `daedalus-fab/fabrication-server.rs#11`. It changes OTLP egress from gRPC port 4317 to the Deployment's HTTP/protobuf endpoint port 4318 and adds a source-level parity regression.

The cluster commit is stacked PR `ORESoftware/k8s-cluster#1211`. It advances the mode-160000 gitlink and both GitOps catalog pins to the exact source repair while retaining the trigger hardening from `#1209`.

## Three-platform proof

Ubuntu 24.04, macOS 15, and Windows Server 2025 independently:

1. check out the exact cluster commit with `submodules: false`;
2. load the five-file source snapshot and its revision/repository/blob manifest;
3. recompute every snapshot file's Git blob SHA and require an exact match with the private source commit;
4. verify `.gitmodules`, the mode-160000 Git index entry, catalog inventory, catalog direct source, and snapshot metadata identify the same repository and commit;
5. prove the deployment source is not materialized beneath the superproject checkout;
6. run unit tests proving an OTLP 4318 endpoint with only 4317 egress fails closed;
7. extract the real snapshot's NATS and OTLP endpoint ports and require the NetworkPolicy to permit both;
8. download the official Kustomize v5.8.1 asset selected for the runner platform;
9. verify the release asset against a hard-coded official SHA-256 digest before safe extraction;
10. render the checksum-pinned `k8s` snapshot twice and require byte-identical output;
11. require exactly one `Deployment`, `Service`, `NetworkPolicy`, and `ExternalSecret` in namespace `daedalus`;
12. reject cluster-scoped resources, a rendered Kubernetes `Secret`, or plaintext `stringData`; and
13. upload endpoint-policy, source-blob, binary, and render digests plus the rendered YAML.

The pinned release assets and digests come from the official `kubernetes-sigs/kustomize` v5.8.1 GitHub release. Archive extraction rejects path traversal and links.

## Layered evidence

The layers remain intentionally distinct:

- static catalog validation works during a private-repository credential outage;
- snapshot rendering proves the exact exported deployment bytes still render;
- endpoint-policy validation proves checked-in egress permits the declared runtime URLs;
- an authenticated GitHub App checkout must separately prove live private-repository reachability.

A passing static catalog check is not treated as proof of source renderability, and a passing source snapshot does not grant repository or cluster credentials.

## Isolation

All repository checkouts disable persisted credentials. The canary uses no user PAT, GitHub App private key, registry credential, Kubernetes credential, Cloudflare token, DNS permission, R2 key, or database access. It runs `kustomize build` only; it never invokes `kubectl`, `argocd`, Helm installation, a registry push, or an apply operation.

Passing this canary does not activate the inert ApplicationSet and does not authorize production reconciliation.

## Coordination

- Owner source repair: `daedalus-fab/fabrication-server.rs#11`
- Stacked exact-pin update: `ORESoftware/k8s-cluster#1211`
- Baseline catalog/trigger repair: `ORESoftware/k8s-cluster#1209`
- Current-main adversarial matrix: `zed-pkg-test/security-adversarial-e2e#6`
- Root dispatcher merge: `zed-pkg/zed-cli#242`
- GitOps roadmap: `ORESoftware/k8s-cluster#1097`
- Linear: `DEN-2724`, `DEN-2725`, `DEN-630`
