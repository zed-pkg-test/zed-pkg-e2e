import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const workflowPath = path.join(
  root,
  '.github',
  'workflows',
  'secure-fleet-token-handoff.yml',
);
const workflow = fs.readFileSync(workflowPath, 'utf8');

test('encrypted handoff starts only from an explicit reviewed START selection', () => {
  assert.match(
    workflow,
    /push:\n\s+branches: \[main\]\n\s+paths:\n\s+- "\.github\/fleet-handoff\/START"/,
  );
  assert.doesNotMatch(
    workflow,
    /paths:\n[\s\S]*?\.github\/workflows\/secure-fleet-token-handoff\.yml/,
  );
  assert.match(workflow, /github\.event\.head_commit\.removed/);
  assert.match(workflow, /START must contain exactly one approved test organization or all/);
  assert.match(workflow, /HANDOFF_ORGANIZATION=%s/);
  assert.match(workflow, /--arg organization "\$handoff_organization"/);
});

test('encrypted handoff validates and dispatches only the selected organization', () => {
  assert.match(workflow, /organizations=\("\$HANDOFF_ORGANIZATION"\)/);
  assert.match(workflow, /gh api "orgs\/\$organization" --silent/);
  assert.match(workflow, /-f organization="\$HANDOFF_ORGANIZATION"/);
  assert.doesNotMatch(workflow, /-f organization=all/);
  assert.match(
    workflow,
    /The \\`\$HANDOFF_ORGANIZATION\\` apply workflow was dispatched/,
  );
});

test('encrypted handoff retains ciphertext-only and actor verification controls', () => {
  assert.match(workflow, /RSA-OAEP-SHA256/);
  assert.match(workflow, /openssl pkeyutl -decrypt/);
  assert.match(workflow, /EXPECTED_LOGIN: ORESoftware/);
  assert.match(workflow, /payload_nonce/);
  assert.match(workflow, /::add-mask::%s/);
  assert.match(workflow, /gh secret set FLEET_GH_TOKEN/);
  assert.doesNotMatch(workflow, /(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}/);
});
