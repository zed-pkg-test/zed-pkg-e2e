import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import { hardenGeneratedIntegrationPolicy } from '../scripts/test-org-fleet-integration-policy.mjs';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const partsDirectory = path.join(root, 'scripts', 'bootstrap-test-org-fleet.parts');
const generatedSource = fs
  .readdirSync(partsDirectory)
  .sort()
  .map((name) => fs.readFileSync(path.join(partsDirectory, name), 'utf8'))
  .join('')
  .replace(/^#![^\n]*\n/, '');
const sourceCertificationDocumentation = fs.readFileSync(
  path.join(root, 'docs', 'test-org-source-certification.md'),
  'utf8',
);

function hardened() {
  return hardenGeneratedIntegrationPolicy(generatedSource);
}

test('generated plans distinguish snapshots, source access, and product certification', () => {
  const source = hardened();
  assert.match(source, /protectedSourceIntegration: true/);
  assert.match(
    source,
    /protectedSourceIntegrationStatus: 'not-executed-unless-protected-lane-runs'/,
  );
  assert.match(source, /productOverlayPreserved: true/);
  assert.match(source, /generic protected lane reports source-access status only/);
  assert.match(source, /source certification requires a product-specific executable overlay/);
  assert.match(source, /A skipped integration job is not source certification/);
});

test('generated integration never falls back to the repository GITHUB_TOKEN', () => {
  const source = hardened();
  assert.doesNotMatch(
    source,
    /secrets\.TEST_FLEET_READ_TOKEN \|\| github\.token/,
  );
  assert.match(source, /test -n \"\$TEST_FLEET_READ_TOKEN\"/);
  assert.match(source, /token: \\?\$\{\{ secrets\.TEST_FLEET_READ_TOKEN \}\}/);
  assert.match(source, /persist-credentials: false/);
});

test('generated integration cancels superseded runs for the same workflow and ref', () => {
  const source = hardened();
  const concurrencyContract = [
    'permissions:\\n',
    '  contents: read\\n\\n',
    'concurrency:\\n',
    '  group: \\${{ github.workflow }}-\\${{ github.ref }}\\n',
    '  cancel-in-progress: true\\n\\n',
    'jobs:\\n',
    '  integration:\\n',
  ].join('');

  assert.equal(source.split(concurrencyContract).length - 1, 1);
});

test('generic status artifact can report source access but never product certification', () => {
  const source = hardened();
  assert.match(source, /name: Record protected source access status/);
  assert.match(source, /if: always\(\)/);
  assert.match(source, /needs: integration/);
  assert.match(source, /const executed = result !== 'skipped'/);
  assert.match(source, /const sourceAccessPassed = result === 'success'/);
  assert.match(source, /certified: false/);
  assert.doesNotMatch(source, /const certified = result === 'success'/);
  assert.match(source, /protected-source-access-passed-product-certification-required/);
  assert.match(source, /protected-source-integration-not-enabled/);
  assert.match(source, /source-integration-status-/);
  assert.match(
    source,
    /actions\/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a/,
  );
});

test('generated file set leaves product-specific overlays outside fleet ownership', () => {
  const source = hardened();
  const generatedFiles = source.slice(
    source.indexOf('const files = new Map(['),
    source.indexOf('const zpkg = renderZpkg', source.indexOf('const files = new Map([')),
  );
  assert.ok(generatedFiles.length > 0);
  for (const productPath of [
    'CONTRACT.md',
    'contract-lock.json',
    'contract-surface.json',
    'scripts/contract-parser.mjs',
    '.github/workflows/api-contract-parity.yml',
  ]) {
    assert.equal(
      generatedFiles.includes(productPath),
      false,
      `${productPath} must remain product-overlay owned`,
    );
  }
  assert.match(source, /Product-specific files outside the generated file set are preserved/);
});

test('source certification documentation preserves the security and evidence meanings', () => {
  assert.match(
    sourceCertificationDocumentation,
    /A skipped lane is \*\*not\*\* source certification/,
  );
  assert.match(sourceCertificationDocumentation, /must not fall back to `github\.token`/);
  assert.match(sourceCertificationDocumentation, /Product-specific overlays are preserved/);
  assert.match(sourceCertificationDocumentation, /generic generated lane/i);
  assert.match(sourceCertificationDocumentation, /source access/i);
  assert.match(sourceCertificationDocumentation, /product-specific executable/i);
  assert.match(sourceCertificationDocumentation, /`executed`/);
  assert.match(sourceCertificationDocumentation, /`certified`/);
  assert.match(sourceCertificationDocumentation, /Never choose an entire conflict side/);
  assert.doesNotMatch(
    sourceCertificationDocumentation,
    /ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}/,
  );
});

test('policy transform is fail-closed when generated seams change', () => {
  assert.throws(
    () => hardenGeneratedIntegrationPolicy(hardened()),
    /expected one generated seam, found 0/,
  );
  assert.throws(
    () =>
      hardenGeneratedIntegrationPolicy(
        generatedSource.replace(
          '          token: \\${{ secrets.TEST_FLEET_READ_TOKEN || github.token }}',
          '          token: changed',
        ),
      ),
    /protected checkout token fallback/,
  );
  assert.throws(
    () =>
      hardenGeneratedIntegrationPolicy(
        generatedSource.replace(
          "    - cron: '17 8 * * *'\\n\\npermissions:\\n  contents: read\\n\\njobs:\\n",
          'generated integration concurrency seam changed',
        ),
      ),
    /generated integration concurrency/,
  );
});
