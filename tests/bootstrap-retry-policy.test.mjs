import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const launcher = fs.readFileSync(path.join(root, 'scripts', 'bootstrap-test-org-fleet.mjs'), 'utf8');

test('primary quota exhaustion enters the bounded retry path', () => {
  assert.match(launcher, /const primaryRemaining = Number\(response\.headers\.get\('x-ratelimit-remaining'\)/);
  assert.match(launcher, /const primaryRateLimit = response\.status === 403 && primaryRemaining === 0;/);
  assert.match(
    launcher,
    /if \(\(primaryRateLimit \|\| secondaryRateLimit \|\| response\.status === 429 \|\| response\.status >= 500\)/,
  );
  assert.match(launcher, /Date\.now\(\) \+ 5000/);
});

test('secondary rate limits do not inherit a non-exhausted primary reset timestamp', () => {
  assert.match(launcher, /const resetDelayMs = primaryRateLimit && resetEpochSeconds > 0/);
  assert.match(launcher, /Math\.min\(180000, 15000 \* \(2 \*\* attempt\)\)/);
  assert.doesNotMatch(launcher, /const resetDelayMs = resetEpochSeconds > 0\s*\?/);
});

test('rate-limit logs expose quota state while git errors redact the token', () => {
  assert.match(launcher, /primaryRateLimit,/);
  assert.match(launcher, /primaryRemaining,/);
  assert.match(launcher, /raw\.replaceAll\(token, '\*\*\*'\)/);
  assert.doesNotMatch(launcher, /log\([^\n]*token/);
});
