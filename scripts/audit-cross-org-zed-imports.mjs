#!/usr/bin/env node

import fs from 'node:fs/promises';
import process from 'node:process';

const configPath = process.argv[2] ?? 'config/cross-org-zed-imports.json';
const token = process.env.GH_TOKEN;
if (!token) {
  throw new Error('GH_TOKEN is required for the live cross-organization audit');
}

const config = JSON.parse(await fs.readFile(configPath, 'utf8'));
if (config.schema !== 'zed.cross-org-import-audit/v1') {
  throw new Error(`unsupported audit schema: ${config.schema}`);
}

const route = (value) => encodeURIComponent(value);
const fetched = new Map();

async function fetchText(repository, ref, path) {
  const key = `${repository}@${ref}:${path}`;
  if (fetched.has(key)) return fetched.get(key);

  const url = `https://api.github.com/repos/${repository}/contents/${path
    .split('/')
    .map(route)
    .join('/')}?ref=${route(ref)}`;
  const response = await fetch(url, {
    headers: {
      accept: 'application/vnd.github.raw+json',
      authorization: `Bearer ${token}`,
      'user-agent': 'zed-pkg-test-cross-org-import-audit/1',
      'x-github-api-version': '2022-11-28',
    },
  });
  if (!response.ok) {
    const body = (await response.text()).slice(0, 400);
    throw new Error(`${key}: GitHub returned ${response.status}: ${body}`);
  }
  const text = await response.text();
  fetched.set(key, text);
  return text;
}

const failures = [];
let filesChecked = 0;

for (const check of config.checks) {
  if (check.testOrganization && !check.repository.split('/')[0].endsWith('-test')) {
    failures.push(`${check.name}: ${check.repository} is not in a *-test organization`);
  }

  for (const file of check.files) {
    const text = await fetchText(check.repository, check.ref, file.path);
    filesChecked += 1;
    for (const expected of file.contains ?? []) {
      if (!text.includes(expected)) {
        failures.push(`${check.name}: ${file.path} is missing ${JSON.stringify(expected)}`);
      }
    }
    for (const forbidden of file.excludes ?? []) {
      if (text.includes(forbidden)) {
        failures.push(`${check.name}: ${file.path} still contains ${JSON.stringify(forbidden)}`);
      }
    }
  }
}

const summary = {
  schema: config.schema,
  checks: config.checks.length,
  filesChecked,
  testOrganizations: [
    ...new Set(
      config.checks
        .filter((check) => check.testOrganization)
        .map((check) => check.repository.split('/')[0]),
    ),
  ].sort(),
  productionOrganizations: [
    ...new Set(
      config.checks
        .filter((check) => !check.testOrganization)
        .map((check) => check.repository.split('/')[0]),
    ),
  ].sort(),
  failures,
};

process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);
if (failures.length > 0) process.exitCode = 1;
