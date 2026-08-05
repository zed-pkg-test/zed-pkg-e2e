#!/usr/bin/env node
// Preserve branch path separators for GitHub's git-ref endpoint while keeping
// every individual segment URL-encoded. The core auditor remains deterministic
// and this small launcher locks the API path behavior at one stable seam.
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const scriptsDirectory = path.join(root, 'scripts');
const auditorPath = path.join(scriptsDirectory, 'audit-test-org-fleet.mjs');
let source = fs.readFileSync(auditorPath, 'utf8').replace(/^#![^\n]*\n/, '');
const original = 'const encodedBranch = route(branch);';
const replacement = "const encodedBranch = branch.split('/').map(route).join('/');";
if (!source.includes(original)) throw new Error('fleet audit branch-path seam changed');
source = source.replace(original, replacement);

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
