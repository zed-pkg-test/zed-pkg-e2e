import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import path from 'node:path';
import test from 'node:test';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const exporter = path.join(root, 'scripts', 'export-test-org-fleet-index.mjs');

function loadIndex() {
  const result = childProcess.spawnSync(process.execPath, [exporter], {
    cwd: root,
    encoding: 'utf8',
    env: { ...process.env, GH_TOKEN: '', GITHUB_TOKEN: '' },
  });
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}

test('exports the exact live 360-repository portfolio', () => {
  const index = loadIndex();
  assert.equal(index.schemaVersion, 1);
  assert.equal(index.pairCount, 19);
  assert.equal(index.retainedRepositoryCount, 22);
  assert.equal(index.specializedRepositoryCount, 319);
  assert.equal(index.governanceRepositoryCount, 19);
  assert.equal(index.managedRepositoryCount, 338);
  assert.equal(index.expectedRepositoryCount, 360);
  assert.deepEqual(index.excludedOrganizations.sort(), ['r2g', 'r2g-test']);
});

test('exports unique expected names for every paired test organization', () => {
  const index = loadIndex();
  for (const organization of index.organizations) {
    assert.match(organization.testOrganization, /-test$/i);
    assert.equal(organization.governanceRepository, '.github');
    assert.equal(
      new Set(organization.expectedRepositories).size,
      organization.expectedRepositories.length,
      organization.testOrganization,
    );
    assert.equal(
      organization.expectedCount,
      organization.retainedCount + organization.specializedCount + 1,
      organization.testOrganization,
    );
    assert.equal(organization.managedCount, organization.specializedCount + 1);
  }
});

test('keeps the canonical high-depth product counts stable', () => {
  const index = loadIndex();
  const byName = new Map(index.organizations.map((item) => [item.testOrganization, item]));
  assert.equal(byName.get('evento-globolo-test').specializedCount, 17);
  assert.equal(byName.get('fiducia-cloud-test').specializedCount, 31);
  assert.equal(byName.get('shared-auth-test').specializedCount, 19);
  assert.equal(byName.get('file-tunnel-test').specializedCount, 17);
  assert.equal(byName.get('hacker-house-medellin-test').specializedCount, 18);
  assert.equal(byName.get('streempilot-test').specializedCount, 20);
});
