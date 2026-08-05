#!/usr/bin/env node
// Preserve branch path separators for GitHub's git-ref endpoint while keeping
// every individual segment URL-encoded. The core auditor remains deterministic
// and this small launcher locks the API path behavior at one stable seam.
import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const auditorPath = path.join(root, 'scripts', 'audit-test-org-fleet.mjs');
let source = fs.readFileSync(auditorPath, 'utf8').replace(/^#![^\n]*\n/, '');
const original = 'const encodedBranch = route(branch);';
const replacement = "const encodedBranch = branch.split('/').map(route).join('/');";
if (!source.includes(original)) throw new Error('fleet audit branch-path seam changed');
source = source.replace(original, replacement);
await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
