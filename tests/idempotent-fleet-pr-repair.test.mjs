import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const sourcePath = path.join(root, 'scripts', 'bootstrap-test-org-fleet.parts', '005.mjs.part');
const source = fs.readFileSync(sourcePath, 'utf8');
const credentialLiteral = /(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}/;

test('idempotent apply repairs missing draft PRs even when generated files are unchanged', () => {
  const governance = source.match(/async function applyGovernance[\s\S]*?async function applyTestRepository/)?.[0] ?? '';
  const repository = source.match(/async function applyTestRepository[\s\S]*?async function mapConcurrent/)?.[0] ?? '';

  assert.match(governance, /const result = await writeTree/);
  assert.match(governance, /await ensurePullRequest/);
  assert.doesNotMatch(governance, /if \(result\.changed\)/);

  assert.match(repository, /const result = await writeTree/);
  assert.match(repository, /await ensurePullRequest/);
  assert.doesNotMatch(repository, /if \(result\.changed\)/);
  assert.equal(credentialLiteral.test(source), false);
});
