import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';

import { readFleetManifest } from '../scripts/read-fleet-manifest.mjs';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const manifest = readFleetManifest(path.join(root, 'bootstrap', 'test-org-fleet.json.gz'));

test('Quaestor adds dedicated tenant, webhook, and recovery risk repositories', () => {
  const pair = manifest.pairs.find((candidate) => candidate.testOrg === 'quaestor-ledger-test');
  assert.ok(pair);
  assert.equal(pair.sourceOrg, 'quaestor-ledger');
  assert.equal(pair.repositories.length, 19);

  const repositories = new Map(pair.repositories.map((repository) => [repository.name, repository]));
  const expected = new Map([
    ['tenant-isolation-e2e', 'security-e2e'],
    ['webhook-auth-replay-e2e', 'security-e2e'],
    ['migration-recovery-e2e', 'database-e2e'],
  ]);

  for (const [name, profile] of expected) {
    const repository = repositories.get(name);
    assert.ok(repository, name);
    assert.equal(repository.state, 'create');
    assert.equal(repository.profile, profile);
    assert.equal(repository.focus.length >= 5, true);
    assert.equal(repository.sources.every((source) => /^[^/]+\/[^/]+$/.test(source)), true);
    assert.deepEqual(repository.imports, []);
  }
});

test('Quaestor extension contains no credential-shaped values', () => {
  const pair = manifest.pairs.find((candidate) => candidate.testOrg === 'quaestor-ledger-test');
  const serialized = JSON.stringify(pair);
  assert.doesNotMatch(serialized, /(?:ghp_|github_pat_|Bearer\s+)[A-Za-z0-9._-]{20,}/i);
});
