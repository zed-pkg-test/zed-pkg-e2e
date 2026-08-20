#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { readFleetManifest } from './read-fleet-manifest.mjs';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const manifest = readFleetManifest(path.join(root, 'bootstrap', 'test-org-fleet.json.gz'));
const token = process.env.GH_TOKEN || process.env.GITHUB_TOKEN;
const apiRoot = process.env.GITHUB_API_URL || 'https://api.github.com';
const concurrency = Math.max(1, Math.min(8, Number(process.env.FLEET_VERIFY_CONCURRENCY || 4)));

if (!token) throw new Error('GH_TOKEN or GITHUB_TOKEN is required for live fleet verification');

const excluded = new Set(['r2g', 'r2g-test']);
const expected = [];
for (const pair of manifest.pairs) {
  const organization = pair.testOrg;
  if (excluded.has(organization.toLowerCase())) {
    throw new Error(`refusing excluded organization in manifest: ${organization}`);
  }
  expected.push({ organization, repository: '.github', kind: 'governance' });
  for (const repository of pair.repositories) {
    expected.push({ organization, repository: repository.name, kind: 'specialized' });
  }
}

const expectedSpecialized = expected.filter((entry) => entry.kind === 'specialized').length;
const expectedGovernance = expected.filter((entry) => entry.kind === 'governance').length;
if (expectedSpecialized !== 322 || expectedGovernance !== 19 || expected.length !== 341) {
  throw new Error(
    `unexpected live verification scope: ${expectedSpecialized} specialized + ` +
      `${expectedGovernance} governance = ${expected.length}`,
  );
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function apiRequest(endpoint, { allow404 = false } = {}) {
  for (let attempt = 0; attempt < 8; attempt += 1) {
    const response = await fetch(`${apiRoot}${endpoint}`, {
      headers: {
        Accept: 'application/vnd.github+json',
        Authorization: `Bearer ${token}`,
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'zed-pkg-test-live-fleet-verifier',
      },
    });
    const text = await response.text();
    if (response.ok) return text ? JSON.parse(text) : null;
    if (allow404 && response.status === 404) return null;

    const secondary = response.status === 403 && /secondary rate limit|abuse detection|temporarily blocked/i.test(text);
    const retryable = secondary || response.status === 429 || response.status >= 500;
    if (retryable && attempt < 7) {
      const retryAfter = Number(response.headers.get('retry-after') || 0) * 1000;
      const backoff = Math.min(120000, (secondary ? 10000 : 1000) * (2 ** attempt));
      await delay(Math.max(retryAfter, backoff));
      continue;
    }
    throw new Error(`GitHub API ${response.status} for ${endpoint}: ${text.slice(0, 500)}`);
  }
  throw new Error(`exhausted retries for ${endpoint}`);
}

async function verifyRepository(entry) {
  const owner = encodeURIComponent(entry.organization);
  const name = encodeURIComponent(entry.repository);
  const repository = await apiRequest(`/repos/${owner}/${name}`, { allow404: true });
  if (!repository) {
    return { ...entry, present: false, defaultBranch: null, defaultBranchPresent: false, openPullRequests: 0, draftPullRequests: 0 };
  }

  const defaultBranch = repository.default_branch || 'main';
  const ref = await apiRequest(
    `/repos/${owner}/${name}/git/ref/heads/${encodeURIComponent(defaultBranch)}`,
    { allow404: true },
  );
  const pulls = await apiRequest(`/repos/${owner}/${name}/pulls?state=open&per_page=100`);
  return {
    ...entry,
    present: true,
    visibility: repository.visibility,
    defaultBranch,
    defaultBranchPresent: Boolean(ref),
    openPullRequests: pulls.length,
    draftPullRequests: pulls.filter((pull) => pull.draft).length,
    pullRequests: pulls.map((pull) => ({
      number: pull.number,
      title: pull.title,
      draft: pull.draft,
      head: pull.head?.ref || null,
      htmlUrl: pull.html_url,
    })),
  };
}

async function mapConcurrent(items, limit, operation) {
  const results = new Array(items.length);
  let cursor = 0;
  async function worker() {
    while (true) {
      const index = cursor;
      cursor += 1;
      if (index >= items.length) return;
      if (index % 25 === 0) process.stderr.write(`verifying ${index + 1}/${items.length}\n`);
      results[index] = await operation(items[index]);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, () => worker()));
  return results;
}

const repositories = await mapConcurrent(expected, concurrency, verifyRepository);
const missingRepositories = repositories.filter((entry) => !entry.present);
const missingDefaultBranches = repositories.filter((entry) => entry.present && !entry.defaultBranchPresent);
const missingOpenPullRequests = repositories.filter((entry) => entry.present && entry.openPullRequests === 0);
const specialized = repositories.filter((entry) => entry.kind === 'specialized');
const governance = repositories.filter((entry) => entry.kind === 'governance');

const summary = {
  generatedAt: new Date().toISOString(),
  expected: {
    organizations: manifest.pairs.length,
    specializedRepositories: expectedSpecialized,
    governanceRepositories: expectedGovernance,
    totalRepositories: expected.length,
  },
  verified: {
    presentRepositories: repositories.length - missingRepositories.length,
    specializedRepositories: specialized.filter((entry) => entry.present).length,
    governanceRepositories: governance.filter((entry) => entry.present).length,
    repositoriesWithDefaultBranch: repositories.length - missingRepositories.length - missingDefaultBranches.length,
    repositoriesWithOpenPullRequest: repositories.filter((entry) => entry.present && entry.openPullRequests > 0).length,
    openPullRequests: repositories.reduce((total, entry) => total + entry.openPullRequests, 0),
    draftPullRequests: repositories.reduce((total, entry) => total + entry.draftPullRequests, 0),
  },
  failures: {
    missingRepositories: missingRepositories.map(({ organization, repository, kind }) => ({ organization, repository, kind })),
    missingDefaultBranches: missingDefaultBranches.map(({ organization, repository, kind, defaultBranch }) => ({ organization, repository, kind, defaultBranch })),
    missingOpenPullRequests: missingOpenPullRequests.map(({ organization, repository, kind }) => ({ organization, repository, kind })),
  },
  repositories,
};

process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);

if (process.env.GITHUB_STEP_SUMMARY) {
  const failureRows = [
    ['Missing repositories', summary.failures.missingRepositories.length],
    ['Missing default branches', summary.failures.missingDefaultBranches.length],
    ['Repositories without an open PR', summary.failures.missingOpenPullRequests.length],
  ];
  const markdown = [
    '# Live test-organization fleet verification',
    '',
    `Generated: ${summary.generatedAt}`,
    '',
    '| Contract | Expected | Verified |',
    '|---|---:|---:|',
    `| Test organizations | ${summary.expected.organizations} | ${new Set(repositories.filter((entry) => entry.present).map((entry) => entry.organization)).size} |`,
    `| Specialized repositories | ${summary.expected.specializedRepositories} | ${summary.verified.specializedRepositories} |`,
    `| Governance repositories | ${summary.expected.governanceRepositories} | ${summary.verified.governanceRepositories} |`,
    `| Total repositories | ${summary.expected.totalRepositories} | ${summary.verified.presentRepositories} |`,
    `| Repositories with a default branch | ${summary.expected.totalRepositories} | ${summary.verified.repositoriesWithDefaultBranch} |`,
    `| Repositories with an open PR | ${summary.expected.totalRepositories} | ${summary.verified.repositoriesWithOpenPullRequest} |`,
    `| Open PRs observed | — | ${summary.verified.openPullRequests} |`,
    `| Draft PRs observed | — | ${summary.verified.draftPullRequests} |`,
    '',
    '| Failure class | Count |',
    '|---|---:|',
    ...failureRows.map(([label, count]) => `| ${label} | ${count} |`),
    '',
    '`r2g` and `r2g-test` are excluded from the manifest-derived verification scope.',
  ];
  fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, `${markdown.join('\n')}\n`);
}

if (missingRepositories.length || missingDefaultBranches.length || missingOpenPullRequests.length) {
  process.exitCode = 1;
}
