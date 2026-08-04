# Authenticated ORAS push certification

This suite certifies the first mutating OCI transport boundary in `zed-cli`.
It starts from a Zed-generated OCI image layout, pushes that exact layout to a
throwaway basic-auth registry through ORAS, and independently resolves the
remote tag back to the expected SHA-256 manifest digest.

The test deliberately does **not** publish to GHCR or any persistent company
registry. The registry, username, password, repository, and tags exist only for
the duration of one GitHub Actions job.

## Security contract

`zed oci push` must select exactly one authentication source:

1. `--username` with `--password-stdin`;
2. an explicit `--registry-config`; or
3. `--anonymous`.

The command is not permitted to discover credentials implicitly from
`~/.docker/config.json`, `$DOCKER_CONFIG/config.json`, Zed registry credentials,
or shared-auth session state. The password-stdin path creates a temporary
Docker-compatible registry config with mode `0600`, passes that exact file to
ORAS, and removes it before returning.

The representative authenticated push runs under `strace`. The suite fails if
it observes an open of an implicit Docker or Zed credential file. Password text
is never included in command arguments or structured evidence, and the harness
fails if it appears in stdout, stderr, syscall evidence, or the evidence JSON.

## Registry and transport contract

The workflow starts an isolated Docker Distribution registry on loopback with
bcrypt-backed HTTP Basic Authentication. `--plain-http` is accepted only because
the destination host is `127.0.0.1`; a non-loopback plain-HTTP destination must
fail before ORAS transport begins.

The candidate must:

1. verify every local OCI descriptor, blob size, SHA-256, media type, and exact
   blob-set membership;
2. require the layout tag to match the destination tag;
3. resolve the existing remote tag through ORAS;
4. copy the layout with `oras cp --from-oci-layout` and an explicit destination
   registry config;
5. resolve the remote tag again; and
6. return success only when the remote digest equals the verified local
   manifest digest.

## Mutation semantics

The contract exercises three remote states:

| State | Required result |
| --- | --- |
| tag missing | `pushed` |
| tag already resolves to the same digest | `already-present`, without another copy |
| tag resolves to a different digest | fail unless `--allow-tag-replacement` is explicit |

After an explicitly permitted replacement, independent ORAS resolution must
return the second layout's digest. A refused replacement must leave the first
digest untouched.

## Negative coverage

The black-box harness also proves that:

- a tampered package blob is rejected before ORAS is executed;
- omitting all authentication modes fails closed;
- plain HTTP is rejected for a non-loopback registry;
- source fixtures and unrecoverable transaction sentinels remain unchanged;
- no persistent `.zed/pack` directory is produced; and
- no `zed-oci-auth-*` temporary directory remains after success or failure.

## Reproducibility

The workflow pins the exact `zed-cli` candidate and fixture commits, pins every
GitHub Action to an immutable commit, requests a specific stable ORAS release,
builds with the candidate's checked-in Cargo lockfile, and grants only
`contents: read`.

Structured command evidence, syscall evidence, the registry logs, and the exact
resolved final digest are uploaded for seven days. No real registry secret is
used or retained.

## Deliberately deferred

This contract does not yet cover:

- digest-verified pull and restore;
- recursive referrer copy;
- SPDX, CycloneDX, in-toto, or signature attachment;
- cloud-registry-specific identity federation;
- client certificates or identity-token authentication; or
- multi-platform index publication across several Zed targets.
