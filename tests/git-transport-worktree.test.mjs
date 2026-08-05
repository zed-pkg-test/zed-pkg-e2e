import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const launcher = fs.readFileSync(path.join(root, 'scripts', 'bootstrap-test-org-fleet.mjs'), 'utf8');

function git(cwd, ...args) {
  return childProcess.execFileSync('git', args, { cwd, encoding: 'utf8' }).trim();
}

test('git credential helper never dirties a generated repository worktree', () => {
  assert.match(launcher, /const worktree = fs\.mkdtempSync\(path\.join\(os\.tmpdir\(\), 'test-org-fleet-worktree-'\)\)/);
  assert.match(launcher, /const credentialDirectory = fs\.mkdtempSync\(path\.join\(os\.tmpdir\(\), 'test-org-fleet-credentials-'\)\)/);
  assert.match(launcher, /const askpass = path\.join\(credentialDirectory, 'git-askpass\.sh'\)/);
  assert.doesNotMatch(launcher, /const askpass = path\.join\(worktree, 'git-askpass\.sh'\)/);
  assert.match(launcher, /\['status', '--porcelain=v1', '--untracked-files=no'\]/);
  assert.match(launcher, /fs\.rmSync\(credentialDirectory, \{ recursive: true, force: true \}\)/);
});

test('an unchanged gitlink keeps an empty worktree directory during idempotent recovery', () => {
  assert.match(launcher, /const gitlinkPath = path\.join\(worktree, gitlink\.path\)/);
  assert.match(launcher, /fs\.mkdirSync\(gitlinkPath, \{ recursive: true \}\)/);

  const repository = fs.mkdtempSync(path.join(os.tmpdir(), 'test-org-fleet-gitlink-'));
  try {
    git(repository, 'init', '--quiet');
    git(repository, 'config', 'user.name', 'test-org-fleet');
    git(repository, 'config', 'user.email', 'test-org-fleet@example.invalid');
    fs.writeFileSync(path.join(repository, 'README.md'), '# fixture\n');
    git(repository, 'add', 'README.md');
    git(repository, 'commit', '--quiet', '-m', 'fixture base');

    const sourceSha = git(repository, 'rev-parse', 'HEAD');
    git(repository, 'update-index', '--add', '--cacheinfo', `160000,${sourceSha},vendor/sdk`);
    fs.mkdirSync(path.join(repository, 'vendor', 'sdk'), { recursive: true });
    git(repository, 'commit', '--quiet', '-m', 'add gitlink');
    assert.equal(git(repository, 'status', '--porcelain=v1', '--untracked-files=no'), '');

    fs.rmSync(path.join(repository, 'vendor', 'sdk'), { recursive: true, force: true });
    assert.equal(git(repository, 'status', '--porcelain=v1', '--untracked-files=no'), 'D vendor/sdk');

    fs.mkdirSync(path.join(repository, 'vendor', 'sdk'), { recursive: true });
    assert.equal(git(repository, 'status', '--porcelain=v1', '--untracked-files=no'), '');
  } finally {
    fs.rmSync(repository, { recursive: true, force: true });
  }
});
