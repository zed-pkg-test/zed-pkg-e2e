# Immutable OCI publication-plan contract

This suite certifies the first OCI interoperability boundary for **zed-pkg**:
`zed oci plan` computes the exact immutable artifact identity without logging
in, contacting a registry, recovering a pending project mutation, or writing a
persistent package output.

It is deliberately narrower than OCI publication. Authentication, blob upload,
manifest push, pull, signature/referrer handling, and registry-specific retries
remain separate follow-up contracts. A planner that passes this suite has
proved what bytes and descriptors a later transport is allowed to publish; it
has not proved transport behavior.

## Immutable inputs

`.github/workflows/oci-plan-contract.yml` accepts one exact 40-character
`zed-pkg/zed-cli` commit. The workflow also pins the three fixture repositories
and every third-party Action to immutable commits. It builds the candidate with
its checked-in lockfile and runs the real release binary.

The initial fixture set is intentionally small but structurally complete:

| Fixture | Contract exercised |
| --- | --- |
| `node-lib` | single-package deterministic planning, tag/digest rejection, and syscall audit |
| `polyglot-lib` | required target selection and independent Node.js, Python, Go, and Rust artifacts |
| `node-app` | required frozen lock provenance, lock layer inclusion, version drift, and digest canonicalization |

## Certified invariants

For every successful plan, the harness asserts:

1. two independent invocations produce byte-identical JSON;
2. the public package identity matches the selected source or polyglot target;
3. the requested mutable tag is preserved while the resolved reference gains
   exactly one canonical `sha256:` digest;
4. that digest equals the OCI manifest descriptor and planned manifest blob;
5. config, package, source-manifest, optional lockfile, and OCI-manifest blobs
   have unique typed descriptors and positive sizes;
6. each blob digest agrees with the corresponding adapter layer;
7. the project tree is byte- and mode-identical before and after planning;
8. no `.zed/pack` directory is left behind; and
9. deliberately unrecoverable `.zpkg-staging` state remains untouched.

The representative `node-lib` run executes under `strace`. Any runtime network
socket syscall fails the test. Opening either the poisoned saved-credentials
file or poisoned refresh-session file also fails. Closed loopback proxies and a
nonexistent registry endpoint provide an additional defense against an
accidental transport call.

## Fail-closed cases

The same executable must reject, without changing the project tree:

- a destination tag that differs from the package version;
- a caller-selected digest instead of a digest derived from the planned bytes;
- a polyglot source without `--target`;
- a dependency-bearing package without `.zpkg.lock`;
- a locked dependency version outside the manifest requirement; and
- a noncanonical uppercase lock digest.

Assertions are not weakened when a candidate changes. A behavior change must
update the shared OCI contract, unit tests, this black-box suite, and its design
rationale together.

## Evidence

Every completed harness invocation writes `evidence/evidence.json`, containing
all commands, exit statuses, stdout/stderr, plan fingerprints, and negative-case
coverage. The syscall trace for the audited run is stored beside it. GitHub
Actions uploads the directory for seven days.

The evidence contains no registry token or account secret: the job grants only
`contents: read`, clears Zed token/password variables, disables persisted
checkout credentials, and runs the planner with unreadable invalid credential
files.

## Local execution

Build the exact candidate and place fixture checkouts beside this repository,
then run:

```bash
python3 scripts/oci_plan_contract.py \
  --zed ../zed-cli/target/release/zed \
  --strace "$(command -v strace)" \
  --node-lib ../node-lib \
  --node-app ../node-app \
  --polyglot-lib ../polyglot-lib \
  --evidence /tmp/zed-oci-plan-evidence
```

Linux with `strace` is required because the credential/network claim is a
syscall-level assertion, not merely a mock-server assumption.
