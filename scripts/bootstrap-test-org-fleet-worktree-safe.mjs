#!/usr/bin/env node
// Compatibility wrapper for idempotent reruns of generated branches containing gitlinks.
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { pathToFileURL } from 'node:url';

const scriptsDirectory = path.dirname(new URL(import.meta.url).pathname);
const launcherPath = path.join(scriptsDirectory, 'bootstrap-test-org-fleet.mjs');
const launcher = fs.readFileSync(launcherPath, 'utf8');

const gitlinkIndexSeam = [
  '      runGit([',
  "        'update-index',",
  "        '--add',",
  "        '--cacheinfo',",
  '        `${gitlink.mode},${gitlink.sha},${gitlink.path}`,',
  '      ]);',
].join('\n');
const gitlinkWorktreePlaceholder =
  "      fs.mkdirSync(gitlinkPath, { recursive: true });";

let patchedLauncher = launcher;
if (!launcher.includes(gitlinkWorktreePlaceholder)) {
  if (!launcher.includes(gitlinkIndexSeam)) {
    throw new Error('bootstrap gitlink index seam changed; refusing an unsafe runtime patch');
  }
  patchedLauncher = launcher.replace(
    gitlinkIndexSeam,
    `${gitlinkIndexSeam}\n${gitlinkWorktreePlaceholder}`,
  );
}

const temporaryLauncher = path.join(
  scriptsDirectory,
  `.bootstrap-test-org-fleet-worktree-safe-${process.pid}-${Date.now()}.mjs`,
);

fs.writeFileSync(temporaryLauncher, patchedLauncher, 'utf8');
try {
  await import(`${pathToFileURL(temporaryLauncher).href}?run=${Date.now()}`);
} finally {
  fs.rmSync(temporaryLauncher, { force: true });
}
