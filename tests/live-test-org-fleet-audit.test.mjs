import assert from 'node:assert/strict';
import test from 'node:test';

import {
  auditFleet,
  diffRepositoryNames,
  expectedRepositoryNames,
  parseArguments,
  renderMarkdown,
} from '../scripts/audit-live-test-org-fleet.mjs';

function repository(name, overrides = {}) {
  return {
    name,
    archived: false,
    disabled: false,
    default_branch: 'main',
    ...overrides,
  };
}

const pair = {
  sourceOrg: 'example',
  testOrg: 'example-test',
  existingRepositories: ['legacy-fixture'],
  repositories: [{ name: 'api-e2e' }, { name: 'web-e2e' }],
};

test('expected set includes governance, retained fixtures, and generated repos', () => {
  assert.deepEqual(
    [...expectedRepositoryNames(pair)].sort(),
    ['.github', 'api-e2e', 'legacy-fixture', 'web-e2e'],
  );
});

test('diff preserves extras while reporting missing canonical repos', () => {
  assert.deepEqual(
    diffRepositoryNames(['.github', 'api-e2e'], ['.github', 'extra-e2e']),
    {
      missing: ['api-e2e'],
      extras: ['extra-e2e'],
      present: ['.github'],
    },
  );
});

test('argument parser supports scoped and strict audits', () => {
  assert.deepEqual(
    parseArguments(['--json', '--strict-extras', '--org', 'example-test']),
    { json: true, strictExtras: true, organizations: ['example-test'] },
  );
  assert.throws(() => parseArguments(['--org']), /requires a value/);
  assert.throws(() => parseArguments(['--unknown']), /unknown argument/);
});

test('fleet audit checks canonical hygiene but only reports production hygiene', async () => {
  const manifest = {
    policy: { excludedOrganizations: ['r2g', 'r2g-test'] },
    pairs: [pair],
  };
  const client = {
    async listOrganizationRepositories(organization) {
      if (organization === 'example') return [repository('api', { archived: true })];
      return [
        repository('.github'),
        repository('api-e2e'),
        repository('web-e2e', { default_branch: '' }),
        repository('extra-e2e'),
      ];
    },
  };
  const report = await auditFleet(manifest, client);
  assert.equal(report.pairCount, 1);
  assert.equal(report.expectedRepositories, 4);
  assert.equal(report.presentRepositories, 3);
  assert.equal(report.missingRepositories, 1);
  assert.equal(report.extraRepositories, 1);
  assert.equal(report.expectedHygieneFindings, 1);
  assert.equal(report.sourceHygieneFindings, 1);
  assert.deepEqual(report.reports[0].missing, ['legacy-fixture']);
  assert.deepEqual(report.reports[0].extras, ['extra-e2e']);
  assert.match(renderMarkdown(report), /legacy-fixture/);
});

test('excluded and unknown test organizations cannot enter scope', async () => {
  const manifest = {
    policy: { excludedOrganizations: ['r2g', 'r2g-test'] },
    pairs: [pair],
  };
  const client = { async listOrganizationRepositories() { return []; } };
  await assert.rejects(
    () => auditFleet(manifest, client, { organizations: ['r2g-test'] }),
    /unknown or excluded/,
  );
});
