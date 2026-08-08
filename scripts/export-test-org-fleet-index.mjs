#!/usr/bin/env node
import path from 'node:path';
import { readFleetManifest } from './read-fleet-manifest.mjs';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const manifest = readFleetManifest(path.join(root, 'bootstrap', 'test-org-fleet.json.gz'));
const excluded = new Set(manifest.policy.excludedOrganizations ?? []);

const organizations = manifest.pairs.map((pair) => {
  if (excluded.has(pair.sourceOrg) || excluded.has(pair.testOrg)) {
    throw new Error(`excluded organization leaked into the fleet: ${pair.testOrg}`);
  }
  const retainedRepositories = [...(pair.existingRepositories ?? [])];
  const specializedRepositories = pair.repositories.map((repository) => repository.name);
  const managedRepositories = ['.github', ...specializedRepositories];
  const expectedRepositories = [...retainedRepositories, ...managedRepositories];
  if (new Set(expectedRepositories).size !== expectedRepositories.length) {
    throw new Error(`duplicate repository names for ${pair.testOrg}`);
  }
  return {
    sourceOrganization: pair.sourceOrg,
    testOrganization: pair.testOrg,
    retainedRepositories,
    governanceRepository: '.github',
    specializedRepositories,
    managedRepositories,
    expectedRepositories,
    retainedCount: retainedRepositories.length,
    specializedCount: specializedRepositories.length,
    managedCount: managedRepositories.length,
    expectedCount: expectedRepositories.length,
  };
});

const result = {
  schemaVersion: 1,
  generatedFrom: 'bootstrap/test-org-fleet.json.gz.b64.parts',
  excludedOrganizations: [...excluded].sort(),
  pairCount: organizations.length,
  retainedRepositoryCount: organizations.reduce((sum, item) => sum + item.retainedCount, 0),
  specializedRepositoryCount: organizations.reduce((sum, item) => sum + item.specializedCount, 0),
  governanceRepositoryCount: organizations.length,
  managedRepositoryCount: organizations.reduce((sum, item) => sum + item.managedCount, 0),
  expectedRepositoryCount: organizations.reduce((sum, item) => sum + item.expectedCount, 0),
  organizations,
};

if (result.pairCount !== 18) throw new Error(`expected 18 pairs, got ${result.pairCount}`);
if (result.retainedRepositoryCount !== 22) throw new Error(`expected 22 retained repositories, got ${result.retainedRepositoryCount}`);
if (result.specializedRepositoryCount !== 301) throw new Error(`expected 301 specialized repositories, got ${result.specializedRepositoryCount}`);
if (result.governanceRepositoryCount !== 18) throw new Error(`expected 18 governance repositories, got ${result.governanceRepositoryCount}`);
if (result.managedRepositoryCount !== 319) throw new Error(`expected 319 managed repositories, got ${result.managedRepositoryCount}`);
if (result.expectedRepositoryCount !== 341) throw new Error(`expected 341 total repositories, got ${result.expectedRepositoryCount}`);
if (!result.excludedOrganizations.map((item) => item.toLowerCase()).includes('r2g')) throw new Error('r2g must remain excluded');
if (!result.excludedOrganizations.map((item) => item.toLowerCase()).includes('r2g-test')) throw new Error('r2g-test must remain excluded');

process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
