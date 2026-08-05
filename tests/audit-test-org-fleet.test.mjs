import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const scriptPath = path.join(root, 'scripts', 'audit-test-org-fleet.mjs');
const launcherPath = path.join(root, 'scripts', 'audit-test-org-fleet-live.mjs');
const workflowPath = path.join(root, '.github', 'workflows', 'audit-test-org-fleet-once.yml');
const credentialLiteral = /(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}/;

test('live audit plan matches the complete physical fleet', () => {
  const result = childProcess.spawnSync(process.execPath, [launcherPath, '--plan-json'], {
    cwd: root,
    encoding: 'utf8',
    env: { ...process.env, GH_TOKEN: '', GITHUB_TOKEN: '' },
  });
  assert.equal(result.status, 0, result.stderr);
  const plan = JSON.parse(result.stdout);
  assert.equal(plan.organizations, 18);
  assert.equal(plan.repositories, 341);
  assert.equal(plan.generatedRepositories, 319);
  assert.equal(plan.retainedRepositories, 22);
  assert.equal(Object.keys(plan.expectedByOrganization).length, 18);
  assert.equal(plan.expectedByOrganization['zed-pkg-test'], 37);
  assert.equal(plan.expectedByOrganization['fiducia-cloud-test'], 32);
  assert.equal(plan.expectedByOrganization['streempilot-test'], 21);
  assert.equal('r2g-test' in plan.expectedByOrganization, false);
});

test('live audit requires exact generated branches and draft PRs without embedding credentials', () => {
  const script = fs.readFileSync(scriptPath, 'utf8');
  const launcher = fs.readFileSync(launcherPath, 'utf8');
  const workflow = fs.readFileSync(workflowPath, 'utf8');

  assert.match(script, /expectedRepositories !== 341/);
  assert.match(script, /expectedGeneratedRepositories !== 319/);
  assert.match(script, /expectedRetainedRepositories !== 22/);
  assert.match(script, /missing generated branch/);
  assert.match(script, /expected one open generated PR/);
  assert.match(script, /is not draft/);
  assert.match(script, /differs from branch/);
  assert.match(script, /visibility is/);
  assert.match(launcher, /branch\.split\('\/'\)\.map\(route\)\.join\('\/'\)/);
  assert.match(launcher, /fleet audit branch-path seam changed/);
  assert.match(workflow, /secrets\.FLEET_GH_TOKEN/);
  assert.match(workflow, /AUDIT_TEST_FLEET_V1/);
  assert.match(workflow, /audit-test-org-fleet-live\.mjs/);
  assert.equal(credentialLiteral.test(script), false);
  assert.equal(credentialLiteral.test(launcher), false);
  assert.equal(credentialLiteral.test(workflow), false);
  assert.doesNotMatch(workflow, /workflow_dispatch:/);
});
