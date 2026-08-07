import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const credentialLiteral = /(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}/;

function read(relativePath) {
  return fs.readFileSync(path.join(root, relativePath), 'utf8');
}

test('full fleet apply validates git transport and defers nonessential topic writes', () => {
  const workflow = read('.github/workflows/apply-test-org-fleet.yml');

  assert.match(workflow, /node --test tests\/test-org-fleet\.test\.mjs tests\/git-transport-worktree\.test\.mjs tests\/fleet-recovery-control\.test\.mjs/);
  assert.match(workflow, /TEST_ORG_FLEET_SKIP_TOPICS: "true"/);
  assert.match(workflow, /max-parallel: 1/);
  assert.match(workflow, /-f authentication=pat|authentication=pat/);
  assert.equal(credentialLiteral.test(workflow), false);
});

test('one-time cancellation stops both superseded writers and nothing else', () => {
  const workflow = read('.github/workflows/cancel-stale-fleet-recovery.yml');
  const runIds = [...workflow.matchAll(/\b309\d{8}\b/g)].map((match) => match[0]);

  assert.deepEqual([...new Set(runIds)], ['30972717804', '30973196337']);
  assert.match(workflow, /actions: write/);
  assert.match(workflow, /contents: read/);
  assert.match(workflow, /--method POST "\$endpoint\/cancel"/);
  assert.equal(credentialLiteral.test(workflow), false);
});

test('v2 dispatcher waits for stale writers and launches one PAT-backed all-org apply', () => {
  const workflow = read('.github/workflows/dispatch-test-org-fleet-once.yml');
  const marker = read('bootstrap/execute-test-org-fleet-v2.once').trim();

  assert.equal(marker, 'APPLY_TEST_FLEET_V2');
  assert.match(workflow, /bootstrap\/execute-test-org-fleet-v2\.once/);
  assert.match(workflow, /STALE_RUN_IDS: "30972717804 30973196337"/);
  assert.match(workflow, /Wait for superseded fleet writers to stop/);
  assert.match(workflow, /gh workflow run apply-test-org-fleet\.yml/);
  assert.match(workflow, /-f organization=all/);
  assert.match(workflow, /-f authentication=pat/);
  assert.match(workflow, /-f confirm=APPLY_TEST_FLEET/);
  assert.doesNotMatch(workflow, /workflow_dispatch:/);
  assert.equal(credentialLiteral.test(workflow), false);
});
