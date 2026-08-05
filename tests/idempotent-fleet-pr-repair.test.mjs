import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const orchestrationPath = path.join(root, 'scripts', 'bootstrap-test-org-fleet.parts', '005.mjs.part');
const pullRequestPath = path.join(root, 'scripts', 'bootstrap-test-org-fleet.parts', '004.mjs.part');
const orchestration = fs.readFileSync(orchestrationPath, 'utf8');
const pullRequests = fs.readFileSync(pullRequestPath, 'utf8');
const credentialLiteral = /(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}/;

test('idempotent apply repairs missing draft PRs even when generated files are unchanged', () => {
  const governance = orchestration.match(/async function applyGovernance[\s\S]*?async function applyTestRepository/)?.[0] ?? '';
  const repository = orchestration.match(/async function applyTestRepository[\s\S]*?async function mapConcurrent/)?.[0] ?? '';

  assert.match(governance, /const result = await writeTree/);
  assert.match(governance, /await ensurePullRequest/);
  assert.doesNotMatch(governance, /if \(result\.changed\)/);

  assert.match(repository, /const result = await writeTree/);
  assert.match(repository, /await ensurePullRequest/);
  assert.doesNotMatch(repository, /if \(result\.changed\)/);
  assert.equal(credentialLiteral.test(orchestration), false);
});

test('generated PR reconciliation retains one draft and closes duplicates or non-drafts', () => {
  const ensurePullRequest = pullRequests.match(/async function ensurePullRequest[\s\S]*?async function ens/)?.[0] ?? '';

  assert.match(ensurePullRequest, /pull\.head\?\.ref === branchName/);
  assert.match(ensurePullRequest, /pull\.base\?\.ref === base/);
  assert.match(ensurePullRequest, /const drafts = matching\.filter/);
  assert.match(ensurePullRequest, /closed duplicate generated pull request/);
  assert.match(ensurePullRequest, /closed non-draft generated pull request/);
  assert.match(ensurePullRequest, /pulls\/\$\{duplicate\.number\}/);
  assert.match(ensurePullRequest, /state: 'closed'/);
  assert.match(ensurePullRequest, /draft: true/);
  assert.equal(credentialLiteral.test(pullRequests), false);
});
