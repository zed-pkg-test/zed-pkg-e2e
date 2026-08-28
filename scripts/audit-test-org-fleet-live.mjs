#!/usr/bin/env node
// Preserve branch path separators for GitHub's git-ref endpoint while keeping
// every individual segment URL-encoded. Harden live audit retry behavior at
// stable seams without changing the deterministic core report implementation.
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const scriptsDirectory = path.join(root, 'scripts');
const auditorPath = path.join(scriptsDirectory, 'audit-test-org-fleet.mjs');
let source = fs.readFileSync(auditorPath, 'utf8').replace(/^#![^\n]*\n/, '');

const originalBranchEncoding = 'const encodedBranch = route(branch);';
const hardenedBranchEncoding = "const encodedBranch = branch.split('/').map(route).join('/');";
if (!source.includes(originalBranchEncoding)) throw new Error('fleet audit branch-path seam changed');
source = source.replace(originalBranchEncoding, hardenedBranchEncoding);

const originalRateLimitBlock = `    const secondary = response.status === 403
      && /secondary rate limit|abuse detection|temporarily blocked/i.test(text);
    if ((secondary || response.status === 429 || response.status >= 500) && attempt < attempts - 1) {
      const retryAfter = Number(response.headers.get('retry-after') ?? 0) * 1000;
      const reset = Number(response.headers.get('x-ratelimit-reset') ?? 0) * 1000;
      const resetDelay = reset > Date.now() ? reset - Date.now() : 0;
      const backoff = secondary
        ? Math.min(120000, 5000 * (2 ** attempt))
        : Math.min(30000, 1000 * (2 ** attempt));
      await sleep(Math.max(retryAfter, resetDelay, backoff));
      continue;
    }`;
const hardenedRateLimitBlock = `    const secondary = response.status === 403
      && /secondary rate limit|abuse detection|temporarily blocked/i.test(text);
    const primary = response.status === 403
      && /API rate limit exceeded/i.test(text);
    if ((secondary || primary || response.status === 429 || response.status >= 500) && attempt < attempts - 1) {
      const retryAfter = Number(response.headers.get('retry-after') ?? 0) * 1000;
      const reset = Number(response.headers.get('x-ratelimit-reset') ?? 0) * 1000;
      const resetDelay = primary && reset > Date.now()
        ? Math.max(0, reset - Date.now() + 1000)
        : 0;
      const backoff = primary
        ? Math.min(60000, 5000 * (2 ** attempt))
        : secondary
          ? Math.min(120000, 5000 * (2 ** attempt))
          : Math.min(30000, 1000 * (2 ** attempt));
      await sleep(Math.max(retryAfter, resetDelay, backoff));
      continue;
    }`;
if (!source.includes(originalRateLimitBlock)) throw new Error('fleet audit rate-limit seam changed');
source = source.replace(originalRateLimitBlock, hardenedRateLimitBlock);

const originalConcurrency = 'mapConcurrent(generatedWork, 6,';
const hardenedConcurrency = 'mapConcurrent(generatedWork, 2,';
if (!source.includes(originalConcurrency)) throw new Error('fleet audit concurrency seam changed');
source = source.replace(originalConcurrency, hardenedConcurrency);

// Import the patched auditor from the scripts directory rather than a data URL.
// This preserves its relative `./read-fleet-manifest.mjs` import under Node 22.
const temporaryAuditorPath = path.join(
  scriptsDirectory,
  `.audit-test-org-fleet-live-${process.pid}-${Date.now()}.mjs`,
);
try {
  fs.writeFileSync(temporaryAuditorPath, source, { encoding: 'utf8', mode: 0o600 });
  await import(pathToFileURL(temporaryAuditorPath).href);
} finally {
  fs.rmSync(temporaryAuditorPath, { force: true });
}
