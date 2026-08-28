#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { pathToFileURL } from 'node:url';
import { readFleetManifest } from './read-fleet-manifest.mjs';

const DEFAULT_API_BASE = 'https://api.github.com';

export function expectedRepositoryNames(pair) {
  return new Set([
    '.github',
    ...(pair.existingRepositories ?? []),
    ...pair.repositories.map((repository) => repository.name),
  ]);
}

export function diffRepositoryNames(expectedNames, actualNames) {
  const expected = new Set(expectedNames);
  const actual = new Set(actualNames);
  return {
    missing: [...expected].filter((name) => !actual.has(name)).sort(),
    extras: [...actual].filter((name) => !expected.has(name)).sort(),
    present: [...expected].filter((name) => actual.has(name)).sort(),
  };
}

export function parseArguments(argv) {
  const options = { json: false, strictExtras: false, organizations: [] };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === '--json') options.json = true;
    else if (argument === '--strict-extras') options.strictExtras = true;
    else if (argument === '--org') options.organizations.push(argv[++index]);
    else if (argument === '--help' || argument === '-h') options.help = true;
    else throw new Error(`unknown argument: ${argument}`);
  }
  if (options.organizations.some((value) => !value)) throw new Error('--org requires a value');
  return options;
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

export function createGitHubClient({
  token = process.env.GH_TOKEN || process.env.GITHUB_TOKEN || '',
  apiBase = process.env.GITHUB_API_URL || DEFAULT_API_BASE,
  fetchImplementation = globalThis.fetch,
} = {}) {
  if (typeof fetchImplementation !== 'function') throw new Error('fetch implementation is required');

  async function request(endpoint) {
    const url = `${apiBase}${endpoint}`;
    for (let attempt = 0; attempt < 5; attempt += 1) {
      const response = await fetchImplementation(url, {
        headers: {
          Accept: 'application/vnd.github+json',
          'X-GitHub-Api-Version': '2022-11-28',
          'User-Agent': 'zed-pkg-test-live-fleet-audit/1',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
      });
      if (response.ok) return response.json();
      const body = await response.text();
      if ((response.status === 429 || response.status >= 500) && attempt < 4) {
        const retryAfterSeconds = Number(response.headers.get('retry-after') ?? 0);
        await sleep(Math.max(retryAfterSeconds * 1000, 1000 * (2 ** attempt)));
        continue;
      }
      throw new Error(`GET ${endpoint} -> ${response.status}: ${body.slice(0, 500)}`);
    }
    throw new Error(`GET ${endpoint} exhausted retries`);
  }

  async function listOrganizationRepositories(organization) {
    const repositories = [];
    for (let page = 1; page <= 20; page += 1) {
      const batch = await request(`/orgs/${encodeURIComponent(organization)}/repos?type=all&sort=full_name&direction=asc&per_page=100&page=${page}`);
      if (!Array.isArray(batch)) throw new Error(`unexpected repository response for ${organization}`);
      repositories.push(...batch);
      if (batch.length < 100) return repositories;
    }
    throw new Error(`repository pagination exceeded 2000 entries for ${organization}`);
  }

  return { listOrganizationRepositories };
}

function repositoryHygiene(repository) {
  const findings = [];
  if (repository.archived) findings.push('archived');
  if (repository.disabled) findings.push('disabled');
  if (!repository.default_branch) findings.push('missing-default-branch');
  return findings;
}

export async function auditFleet(manifest, client, { organizations = [] } = {}) {
  const selected = new Set(organizations.map((value) => value.toLowerCase()));
  const excluded = new Set((manifest.policy.excludedOrganizations ?? []).map((value) => value.toLowerCase()));
  const pairs = manifest.pairs.filter((pair) => selected.size === 0 || selected.has(pair.testOrg.toLowerCase()));
  if (selected.size > 0 && pairs.length !== selected.size) {
    const known = new Set(pairs.map((pair) => pair.testOrg.toLowerCase()));
    const unknown = [...selected].filter((name) => !known.has(name));
    throw new Error(`unknown or excluded test organizations: ${unknown.join(', ')}`);
  }

  const reports = [];
  for (const pair of pairs) {
    if (excluded.has(pair.sourceOrg.toLowerCase()) || excluded.has(pair.testOrg.toLowerCase())) {
      throw new Error(`excluded organization entered audit scope: ${pair.testOrg}`);
    }
    const [sourceRepositories, testRepositories] = await Promise.all([
      client.listOrganizationRepositories(pair.sourceOrg),
      client.listOrganizationRepositories(pair.testOrg),
    ]);
    const expected = expectedRepositoryNames(pair);
    const diff = diffRepositoryNames(expected, testRepositories.map((repository) => repository.name));
    const expectedHygiene = testRepositories
      .filter((repository) => expected.has(repository.name))
      .flatMap((repository) => repositoryHygiene(repository).map((finding) => ({ repository: repository.name, finding })));
    const sourceHygiene = sourceRepositories
      .flatMap((repository) => repositoryHygiene(repository).map((finding) => ({ repository: repository.name, finding })));
    reports.push({
      sourceOrganization: pair.sourceOrg,
      testOrganization: pair.testOrg,
      expectedRepositories: expected.size,
      visibleTestRepositories: testRepositories.length,
      visibleSourceRepositories: sourceRepositories.length,
      present: diff.present,
      missing: diff.missing,
      extras: diff.extras,
      expectedHygiene,
      sourceHygiene,
    });
  }

  return {
    generatedAt: new Date().toISOString(),
    schemaVersion: 1,
    pairCount: reports.length,
    expectedRepositories: reports.reduce((sum, report) => sum + report.expectedRepositories, 0),
    presentRepositories: reports.reduce((sum, report) => sum + report.present.length, 0),
    missingRepositories: reports.reduce((sum, report) => sum + report.missing.length, 0),
    extraRepositories: reports.reduce((sum, report) => sum + report.extras.length, 0),
    expectedHygieneFindings: reports.reduce((sum, report) => sum + report.expectedHygiene.length, 0),
    sourceHygieneFindings: reports.reduce((sum, report) => sum + report.sourceHygiene.length, 0),
    reports,
  };
}

export function renderMarkdown(report) {
  const lines = [
    '# Live paired test-fleet audit',
    '',
    `Generated: ${report.generatedAt}`,
    '',
    `Managed pairs: **${report.pairCount}**`,
    '',
    `Canonical repositories present: **${report.presentRepositories}/${report.expectedRepositories}**`,
    '',
    `Missing: **${report.missingRepositories}** · Extras preserved: **${report.extraRepositories}** · Canonical hygiene findings: **${report.expectedHygieneFindings}**`,
    '',
    '| Production org | Test org | Canonical | Present | Missing | Extras | Hygiene |',
    '|---|---|---:|---:|---:|---:|---:|',
  ];
  for (const item of report.reports) {
    lines.push(`| \`${item.sourceOrganization}\` | \`${item.testOrganization}\` | ${item.expectedRepositories} | ${item.present.length} | ${item.missing.length} | ${item.extras.length} | ${item.expectedHygiene.length} |`);
  }
  const missing = report.reports.filter((item) => item.missing.length > 0);
  if (missing.length) {
    lines.push('', '## Missing canonical repositories', '');
    for (const item of missing) lines.push(`- **${item.testOrganization}:** ${item.missing.map((name) => `\`${name}\``).join(', ')}`);
  }
  const hygiene = report.reports.filter((item) => item.expectedHygiene.length > 0);
  if (hygiene.length) {
    lines.push('', '## Canonical repository hygiene findings', '');
    for (const item of hygiene) {
      for (const finding of item.expectedHygiene) lines.push(`- **${item.testOrganization}/${finding.repository}:** ${finding.finding}`);
    }
  }
  lines.push('', 'Extras are reported but preserved; use `--strict-extras` only for an intentional exact-equality audit.', '');
  return lines.join('\n');
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  if (options.help) {
    console.log('Usage: node scripts/audit-live-test-org-fleet.mjs [--json] [--strict-extras] [--org <test-org>]');
    return;
  }
  const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
  const manifest = readFleetManifest(path.join(root, 'bootstrap', 'test-org-fleet.json.gz'));
  const report = await auditFleet(manifest, createGitHubClient(), options);
  const markdown = renderMarkdown(report);
  if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, `${markdown}\n`);
  if (options.json) process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
  else process.stdout.write(`${markdown}\n`);
  const failure = report.missingRepositories > 0
    || report.expectedHygieneFindings > 0
    || (options.strictExtras && report.extraRepositories > 0);
  if (failure) process.exitCode = 1;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
