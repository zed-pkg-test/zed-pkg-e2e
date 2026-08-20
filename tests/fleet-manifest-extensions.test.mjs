import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { applyFleetManifestExtensions } from '../scripts/fleet-manifest-extensions.mjs';

function fixture() {
  return {
    schemaVersion: 1,
    pairs: [
      {
        sourceOrg: 'quaestor-ledger',
        testOrg: 'quaestor-ledger-test',
        existingRepositories: [],
        repositories: [{ name: 'existing-e2e' }],
      },
    ],
  };
}

function withExtension(extension, callback) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'fleet-extension-test-'));
  const manifestPath = path.join(directory, 'test-org-fleet.json.gz');
  fs.writeFileSync(path.join(directory, 'test-org-fleet.extensions.json'), `${JSON.stringify(extension, null, 2)}\n`);
  try {
    return callback(manifestPath);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

test('extensions append repositories to one existing organization pair', () => {
  const manifest = fixture();
  withExtension({
    schemaVersion: 1,
    pairs: [{
      sourceOrg: 'quaestor-ledger',
      testOrg: 'quaestor-ledger-test',
      repositories: [{ name: 'tenant-isolation-e2e', state: 'create' }],
    }],
  }, (manifestPath) => applyFleetManifestExtensions(manifest, manifestPath));

  assert.deepEqual(
    manifest.pairs[0].repositories.map((repository) => repository.name),
    ['existing-e2e', 'tenant-isolation-e2e'],
  );
});

test('extensions reject duplicate repository names', () => {
  withExtension({
    schemaVersion: 1,
    pairs: [{
      testOrg: 'quaestor-ledger-test',
      repositories: [{ name: 'existing-e2e' }],
    }],
  }, (manifestPath) => {
    assert.throws(
      () => applyFleetManifestExtensions(fixture(), manifestPath),
      /duplicates repository quaestor-ledger-test\/existing-e2e/,
    );
  });
});

test('extensions reject unknown organizations and source-pair mismatches', () => {
  withExtension({
    schemaVersion: 1,
    pairs: [{ testOrg: 'missing-test', repositories: [{ name: 'new-e2e' }] }],
  }, (manifestPath) => {
    assert.throws(() => applyFleetManifestExtensions(fixture(), manifestPath), /unknown test organization/);
  });

  withExtension({
    schemaVersion: 1,
    pairs: [{
      sourceOrg: 'wrong-source',
      testOrg: 'quaestor-ledger-test',
      repositories: [{ name: 'new-e2e' }],
    }],
  }, (manifestPath) => {
    assert.throws(() => applyFleetManifestExtensions(fixture(), manifestPath), /source organization mismatch/);
  });
});

test('no extension file leaves the manifest unchanged', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'fleet-extension-test-'));
  try {
    const manifest = fixture();
    assert.equal(
      applyFleetManifestExtensions(manifest, path.join(directory, 'test-org-fleet.json.gz')),
      manifest,
    );
    assert.equal(manifest.pairs[0].repositories.length, 1);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
