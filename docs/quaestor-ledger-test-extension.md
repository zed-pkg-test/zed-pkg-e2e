# Quaestor Ledger test-fleet extension

The additive fleet overlay provisions three focused repositories in `quaestor-ledger-test` without rewriting the retained compressed base manifest:

| Repository | Profile | Required risk coverage |
|---|---|---|
| `tenant-isolation-e2e` | `security-e2e` | Cross-tenant read/write rejection, tenant-claim mismatch, idempotency namespace isolation, and audit ownership. |
| `webhook-auth-replay-e2e` | `security-e2e` | HMAC verification, timestamp skew, replay rejection, payload tamper rejection, and redacted evidence. |
| `migration-recovery-e2e` | `database-e2e` | Forward/backward migration safety, backup restore, point-in-time recovery, closed-period durability, and post-recovery integrity. |

`readFleetManifest()` and the bootstrap launcher both apply the same extension file. They reject duplicate repository names, duplicate pair patches, unknown test organizations, and source/test organization mismatches before any GitHub write.

Validate the composed fleet before applying it:

```sh
node scripts/validate-test-org-fleet.mjs
node --test tests/fleet-manifest-extensions.test.mjs tests/quaestor-fleet-extension.test.mjs tests/test-org-fleet.test.mjs
node scripts/bootstrap-test-org-fleet.mjs --dry-run --summary-json --org quaestor-ledger-test
```

Repository creation remains credential-gated. The apply workflow must use a short-lived GitHub App token or organization-managed `FLEET_GH_TOKEN`; credentials must never be committed to the manifest, overlay, generated test plans, or workflow logs.
