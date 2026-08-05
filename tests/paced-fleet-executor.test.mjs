import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const listScript = path.join(root, 'scripts', 'list-test-org-fleet-repositories.mjs');
const workflow = fs.readFileSync(path.join(root, '.github', 'workflows', 'finish-test-org-fleet-paced.yml'), 'utf8');
const marker = fs.readFileSync(path.join(root, 'bootstrap', 'execute-test-org-fleet-paced.once'), 'utf8').trim();
const credentialLiteral = /(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}/;

const organizations = [
  'fiducia-cloud-test',
  'evento-globolo-test',
  'opto-sync-test',
  'quaestor-ledger-test',
  'memebank-test',
  'scintilla-run-test',
  'file-tunnel-test',
  'shared-auth-test',
  'hypesiege-test',
  'streempilot-test',
  'sonus-auris-test',
  'messaging-intel-test',
  '3fa-app-test',
  'declarative-migrations-test',
  'cliptown-test',
  'claritas-viz-test',
  'embedded-alerts-test',
];

test('paced executor lists exactly 287 specialized repositories', () => {
  let total = 0;
  for (const organization of organizations) {
    const result = childProcess.spawnSync(process.execPath, [listScript, '--org', organization, '--json'], {
      cwd: root,
      encoding: 'utf8',
    });
    assert.equal(result.status, 0, result.stderr);
    const plan = JSON.parse(result.stdout);
    assert.equal(plan.organization, organization);
    assert.equal(plan.repositoryCount, plan.repositories.length);
    assert.equal(new Set(plan.repositories).size, plan.repositories.length);
    total += plan.repositoryCount;
  }
  assert.equal(total, 287);
});

test('paced executor rejects excluded organizations and paces write-producing repositories', () => {
  const excluded = childProcess.spawnSync(process.execPath, [listScript, '--org', 'r2g-test'], {
    cwd: root,
    encoding: 'utf8',
  });
  assert.notEqual(excluded.status, 0);
  assert.match(excluded.stderr, /refusing excluded organization/);

  assert.equal(marker, 'APPLY_TEST_FLEET_PACED_V2');
  assert.match(workflow, /group: apply-test-org-fleet-all/);
  assert.match(workflow, /cancel-in-progress: true/);
  assert.match(workflow, /--repo "\$repository"/);
  assert.match(workflow, /for attempt in 1 2 3/);
  assert.match(workflow, /sleep 15/);
  assert.match(workflow, /TEST_ORG_FLEET_SKIP_TOPICS: "true"/);
  assert.match(workflow, /test "\$total" -eq 287/);
  assert.match(workflow, /tests\/bootstrap-retry-policy\.test\.mjs/);
  assert.equal(workflow.includes('r2g-test'), false);
  assert.equal(credentialLiteral.test(workflow), false);
});
