#!/usr/bin/env node
import path from 'node:path';
import process from 'node:process';
import { readFleetManifest } from './read-fleet-manifest.mjs';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const manifest = readFleetManifest(path.join(root, 'bootstrap', 'test-org-fleet.json.gz'));
const organizationIndex = process.argv.indexOf('--org');
const organization = organizationIndex >= 0 ? process.argv[organizationIndex + 1] : '';
const json = process.argv.includes('--json');

if (!organization) throw new Error('--org <test-org> is required');
if ((manifest.policy.excludedOrganizations ?? []).includes(organization)) {
  throw new Error(`refusing excluded organization ${organization}`);
}
const pair = manifest.pairs.find((candidate) => candidate.testOrg === organization);
if (!pair) throw new Error(`organization is not in the fleet manifest: ${organization}`);
const repositories = pair.repositories.map((repository) => repository.name);
if (new Set(repositories).size !== repositories.length) {
  throw new Error(`duplicate repository names for ${organization}`);
}

if (json) {
  process.stdout.write(`${JSON.stringify({
    organization,
    sourceOrganization: pair.sourceOrg,
    governanceRepository: '.github',
    repositories,
    repositoryCount: repositories.length,
  }, null, 2)}\n`);
} else {
  process.stdout.write(`${repositories.join('\n')}\n`);
}
