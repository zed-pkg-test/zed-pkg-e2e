import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const validatorTemplatePart = path.join(
  root,
  'scripts',
  'bootstrap-test-org-fleet.parts',
  '002.mjs.part',
);

function git(cwd, ...args) {
  return childProcess.execFileSync('git', args, {
    cwd,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  });
}

test('generated validator renders a recursive git tree scan', () => {
  const source = fs.readFileSync(validatorTemplatePart, 'utf8');
  assert.match(source, /\['ls-tree', '-r', 'HEAD'\]/);
  assert.doesNotMatch(source, /\['ls-tree', 'HEAD'\]/);
});

test('recursive tree inspection exposes nested gitlinks and missing paths stay absent', () => {
  const repository = fs.mkdtempSync(path.join(os.tmpdir(), 'nested-gitlink-'));
  try {
    git(repository, 'init', '--quiet');
    git(repository, 'config', 'user.name', 'test-org-fleet');
    git(repository, 'config', 'user.email', 'test-org-fleet@example.invalid');
    fs.writeFileSync(path.join(repository, 'README.md'), '# fixture\n');
    git(repository, 'add', 'README.md');
    git(repository, 'commit', '--quiet', '-m', 'base');

    const pinnedCommit = '1111111111111111111111111111111111111111';
    const nestedPath = 'vendor/example-client';
    git(
      repository,
      'update-index',
      '--add',
      '--cacheinfo',
      `160000,${pinnedCommit},${nestedPath}`,
    );
    git(repository, 'commit', '--quiet', '-m', 'add nested gitlink');

    const topLevel = git(repository, 'ls-tree', 'HEAD');
    const recursive = git(repository, 'ls-tree', '-r', 'HEAD');

    assert.doesNotMatch(topLevel, /vendor\/example-client/);
    assert.match(topLevel, /040000 tree [0-9a-f]{40}\tvendor/);
    assert.match(
      recursive,
      new RegExp(`160000 commit ${pinnedCommit}\\t${nestedPath}`),
    );
    assert.doesNotMatch(recursive, /vendor\/missing-client/);
  } finally {
    fs.rmSync(repository, { recursive: true, force: true });
  }
});
