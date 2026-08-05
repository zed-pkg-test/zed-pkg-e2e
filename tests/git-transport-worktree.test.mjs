import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const launcher = fs.readFileSync(path.join(root, 'scripts', 'bootstrap-test-org-fleet.mjs'), 'utf8');

test('git credential helper never dirties a generated repository worktree', () => {
  assert.match(launcher, /const worktree = fs\.mkdtempSync\(path\.join\(os\.tmpdir\(\), 'test-org-fleet-worktree-'\)\)/);
  assert.match(launcher, /const credentialDirectory = fs\.mkdtempSync\(path\.join\(os\.tmpdir\(\), 'test-org-fleet-credentials-'\)\)/);
  assert.match(launcher, /const askpass = path\.join\(credentialDirectory, 'git-askpass\.sh'\)/);
  assert.doesNotMatch(launcher, /const askpass = path\.join\(worktree, 'git-askpass\.sh'\)/);
  assert.match(launcher, /\['status', '--porcelain=v1', '--untracked-files=no'\]/);
  assert.match(launcher, /fs\.rmSync\(credentialDirectory, \{ recursive: true, force: true \}\)/);
});
