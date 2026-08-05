import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const launcher = fs.readFileSync(path.join(root, 'scripts', 'bootstrap-test-org-fleet.mjs'), 'utf8');

test('secondary rate limits do not inherit a non-exhausted primary reset timestamp', () => {
  assert.match(launcher, /const primaryRemaining = Number\(response\.headers\.get\('x-ratelimit-remaining'\)/);
  assert.match(launcher, /const primaryRateLimit = primaryRemaining === 0;/);
  assert.match(launcher, /const resetDelayMs = primaryRateLimit && resetEpochSeconds > 0/);
  assert.match(launcher, /Math\.min\(180000, 15000 \* \(2 \*\* attempt\)\)/);
  assert.doesNotMatch(launcher, /const resetDelayMs = resetEpochSeconds > 0\s*\?/);
});

test('rate-limit logs expose remaining primary quota without exposing credentials', () => {
  assert.match(launcher, /primaryRemaining,/);
  assert.doesNotMatch(launcher, /Authorization: `Bearer \$\{token\}`[^]*log\(/);
});
