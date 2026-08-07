// A `workflow_run` trigger is resolved by GitHub against the `name:` declared
// by workflows on the default branch. A reference to a name no workflow
// declares is accepted silently at push time and simply never fires, so the
// dependent workflow looks configured while never running once. That is exactly
// how `verify-live-test-org-fleet.yml` sat dead: it waited on "run authoritative
// test organization fleet on ARM64", a workflow that only ever existed on the
// unmerged `agent/run-fleet-arm64` branch.
//
// Parsed line-by-line on purpose. The repository carries ~70 workflows and
// pulling in a YAML dependency for two keys is not worth the supply-chain
// surface; the two keys involved (`workflows:` under `workflow_run:`, and the
// top-level `name:`) are unambiguous in block style.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const workflowDir = path.join(root, '.github', 'workflows');

function indentOf(line) {
  return line.length - line.trimStart().length;
}

function declaredName(lines, file) {
  for (const line of lines) {
    if (line.startsWith('name:')) return line.slice(5).trim().replace(/^['"]|['"]$/g, '');
  }
  return path.basename(file);
}

// Returns the workflow names referenced by every `workflow_run:` trigger.
function workflowRunReferences(lines) {
  const references = [];
  for (let i = 0; i < lines.length; i += 1) {
    if (!lines[i].includes('workflow_run:')) continue;
    const triggerIndent = indentOf(lines[i]);

    for (let j = i + 1; j < lines.length; j += 1) {
      const line = lines[j];
      if (line.trim() && indentOf(line) <= triggerIndent) break;
      if (!line.includes('workflows:')) continue;

      const inline = line.slice(line.indexOf('workflows:') + 'workflows:'.length).trim();
      if (inline.startsWith('[')) {
        for (const entry of inline.replace(/^\[|\]$/g, '').split(',')) {
          const value = entry.trim().replace(/^['"]|['"]$/g, '');
          if (value) references.push(value);
        }
      } else {
        for (let k = j + 1; k < lines.length; k += 1) {
          if (!lines[k].trim()) continue;
          if (!lines[k].trimStart().startsWith('-')) break;
          const value = lines[k].trimStart().slice(1).trim().replace(/^['"]|['"]$/g, '');
          if (value) references.push(value);
        }
      }
      break;
    }
  }
  return references;
}

const files = fs
  .readdirSync(workflowDir)
  .filter((entry) => entry.endsWith('.yml') || entry.endsWith('.yaml'))
  .map((entry) => path.join(workflowDir, entry));

const parsed = new Map(files.map((file) => [file, fs.readFileSync(file, 'utf8').split('\n')]));
const declaredNames = new Set([...parsed].map(([file, lines]) => declaredName(lines, file)));
const basenames = new Set(files.map((file) => path.basename(file)));

test('every workflow_run trigger names a workflow this repository declares', () => {
  assert.ok(files.length > 0, 'no workflows found to check');

  const dangling = [];
  for (const [file, lines] of parsed) {
    for (const reference of workflowRunReferences(lines)) {
      if (!declaredNames.has(reference) && !basenames.has(reference)) {
        dangling.push(`${path.basename(file)} waits on '${reference}' — no workflow declares it`);
      }
    }
  }

  assert.deepEqual(
    dangling,
    [],
    `dead workflow_run trigger(s); these workflows can never fire:\n  ${dangling.join('\n  ')}`,
  );
});

test('the live fleet verifier chains from fleet writers that exist on main', () => {
  const verifier = path.join(workflowDir, 'verify-live-test-org-fleet.yml');
  const references = workflowRunReferences(parsed.get(verifier));

  assert.ok(references.length > 0, 'the live fleet verifier lost its workflow_run chain');
  for (const reference of references) {
    assert.ok(
      declaredNames.has(reference),
      `live fleet verifier waits on undeclared workflow '${reference}'`,
    );
  }

  // The verifier must also be reachable without an upstream run, so a change to
  // the verifier itself is exercised by its own pull request.
  const text = parsed.get(verifier).join('\n');
  assert.match(text, /^ {2}pull_request:$/m, 'the verifier must run on its own pull requests');
  assert.match(text, /^ {2}workflow_dispatch:$/m, 'the verifier must stay manually dispatchable');
});
