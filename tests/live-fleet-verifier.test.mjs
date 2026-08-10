import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { readFleetManifest } from '../scripts/read-fleet-manifest.mjs';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const verifierPath = path.join(root, 'scripts', 'verify-live-test-org-fleet.mjs');
const verifier = fs.readFileSync(verifierPath, 'utf8');
const manifest = readFleetManifest(path.join(root, 'bootstrap', 'test-org-fleet.json.gz'));

test('live verifier parses and derives the complete non-r2g fleet', () => {
  const syntax = childProcess.spawnSync(process.execPath, ['--check', verifierPath], {
    cwd: root,
    encoding: 'utf8',
  });
  assert.equal(syntax.status, 0, syntax.stderr);

  const specialized = manifest.pairs.reduce((total, pair) => total + pair.repositories.length, 0);
  assert.ok(manifest.pairs.length > 0);
  assert.ok(specialized > manifest.pairs.length);
  assert.equal(
    manifest.pairs.some((pair) => ['r2g', 'r2g-test'].includes(pair.testOrg.toLowerCase())),
    false,
  );

  assert.doesNotMatch(verifier, /expectedSpecialized !== \d+/);
  assert.doesNotMatch(verifier, /expectedGovernance !== \d+/);
  assert.doesNotMatch(verifier, /expected\.length !== \d+/);
  assert.match(verifier, /git\/ref\/heads/);
  assert.match(verifier, /pulls\?state=open&per_page=100/);
  assert.match(verifier, /missingOpenPullRequests/);
  assert.match(verifier, /repositoriesWithOpenPullRequest/);
  assert.doesNotMatch(verifier, /(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}/);
});
