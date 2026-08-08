#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const partsDir = path.join(root, 'bootstrap', 'test-org-fleet.json.gz.b64.parts');
const targetOrg = 'hacker-house-medellin-test';

function importsFor(source, pathName, nativePackage = null) {
  const imports = [
    { source, mode: 'git-submodule', path: pathName },
    { source, mode: 'zed' },
  ];
  if (nativePackage) imports.push({ source, mode: 'native-package', ...nativePackage });
  return imports;
}

function repository({ name, profile, sources, focus, imports = [], services = [], topic, matrix }) {
  return {
    name,
    profile,
    sources,
    focus,
    imports,
    services,
    topics: ['e2e', 'test-harness', topic],
    state: 'create',
    ...(matrix ? { matrix } : {}),
  };
}

const hhmPair = {
  sourceOrg: 'hacker-house-medellin',
  testOrg: targetOrg,
  defaultVisibility: 'private',
  existingRepositories: [],
  repositories: [
    repository({
      name: 'mash-web-e2e',
      profile: 'browser-e2e',
      sources: [
        'hacker-house-medellin/hhm-mash-web',
        'hacker-house-medellin/hhm-clients',
        'hacker-house-medellin/hhm-interfaces',
      ],
      focus: [
        'Maud Axum SeaORM Supabase HTMX',
        'WebSocket updates',
        'Chromium Firefox WebKit',
        'accessibility and responsive layouts',
        'authentication and CSRF boundaries',
      ],
      imports: importsFor('hacker-house-medellin/hhm-clients', 'vendor/hhm-clients'),
      topic: 'browser',
    }),
    repository({
      name: 'leptos-web-e2e',
      profile: 'browser-e2e',
      sources: [
        'hacker-house-medellin/hhm-leptos-web',
        'hacker-house-medellin/hhm-clients',
        'hacker-house-medellin/hhm-interfaces',
      ],
      focus: [
        'SSR and hydration',
        'resident and operator routing',
        'Chromium Firefox WebKit',
        'accessibility',
        'offline and reconnect behavior',
      ],
      imports: importsFor('hacker-house-medellin/hhm-interfaces', 'vendor/hhm-interfaces'),
      topic: 'browser',
    }),
    repository({
      name: 'dioxus-web-e2e',
      profile: 'browser-e2e',
      sources: [
        'hacker-house-medellin/hhm-dioxus-web',
        'hacker-house-medellin/hhm-clients',
        'hacker-house-medellin/hhm-interfaces',
      ],
      focus: [
        'SSR and hydration',
        'community command-center routing',
        'Chromium Firefox WebKit',
        'accessibility',
        'offline and reconnect behavior',
      ],
      imports: importsFor('hacker-house-medellin/hhm-interfaces', 'vendor/hhm-interfaces'),
      topic: 'browser',
    }),
    repository({
      name: 'api-contract-e2e',
      profile: 'api-contract',
      sources: [
        'hacker-house-medellin/hhm-api',
        'hacker-house-medellin/hhm-interfaces',
        'hacker-house-medellin/hhm-clients',
      ],
      focus: [
        'applications memberships stays rooms desks and events',
        'pagination and validation',
        'authorization',
        'idempotency',
        'error-shape compatibility',
      ],
      imports: importsFor('hacker-house-medellin/hhm-interfaces', 'vendor/hhm-interfaces'),
      services: ['postgres'],
      topic: 'api-contract',
    }),
    repository({
      name: 'websocket-contract-e2e',
      profile: 'protocol-e2e',
      sources: [
        'hacker-house-medellin/hhm-api',
        'hacker-house-medellin/hhm-interfaces',
        'hacker-house-medellin/hhm-clients',
      ],
      focus: [
        'typed event envelopes',
        'authorization before upgrade',
        'resume cursors',
        'duplicate delivery',
        'slow consumer and reconnect behavior',
      ],
      imports: importsFor('hacker-house-medellin/hhm-interfaces', 'vendor/hhm-interfaces'),
      topic: 'protocol',
    }),
    repository({
      name: 'cli-contract-e2e',
      profile: 'cli-contract',
      sources: [
        'hacker-house-medellin/hhm-cli',
        'hacker-house-medellin/hhm-interfaces',
      ],
      focus: [
        'flags-2-env precedence',
        'JSON and human output',
        'non-interactive automation',
        'secret redaction',
        'Linux macOS Windows argument parity',
      ],
      imports: importsFor('hacker-house-medellin/hhm-interfaces', 'vendor/hhm-interfaces'),
      topic: 'cli-contract',
    }),
    repository({
      name: 'sync-offline-e2e',
      profile: 'interop-e2e',
      sources: [
        'hacker-house-medellin/hhm-sync',
        'hacker-house-medellin/hhm-interfaces',
        'opto-sync/syncer.rs',
      ],
      focus: [
        'opto-sync merge semantics',
        'offline mutation replay',
        'conflict resolution',
        'crash recovery',
        'WebSocket catch-up',
      ],
      imports: [
        ...importsFor('hacker-house-medellin/hhm-interfaces', 'vendor/hhm-interfaces'),
        { source: 'opto-sync/syncer.rs', mode: 'git-submodule', path: 'vendor/syncer-rs' },
        { source: 'opto-sync/syncer.rs', mode: 'zed' },
      ],
      services: ['postgres'],
      topic: 'interop',
    }),
    repository({
      name: 'infra-compose-e2e',
      profile: 'infra-e2e',
      sources: [
        'hacker-house-medellin/hhm-infra',
        'hacker-house-medellin/hhm-api',
        'hacker-house-medellin/hhm-mash-web',
      ],
      focus: [
        'Docker Compose startup and health',
        'Caddy routing and WebSocket upgrades',
        'migration ordering',
        'backup and restore',
        'credential-free pull-request validation',
      ],
      services: ['postgres'],
      topic: 'infra',
    }),
    repository({
      name: 'postgres-supabase-e2e',
      profile: 'database-e2e',
      sources: [
        'hacker-house-medellin/hhm-api',
        'hacker-house-medellin/hhm-infra',
        'hacker-house-medellin/hhm-interfaces',
      ],
      focus: [
        'Postgres migrations',
        'Supabase-compatible row-level security',
        'tenant and role constraints',
        'rollback and seed isolation',
        'audit-log durability',
      ],
      services: ['postgres'],
      topic: 'database',
    }),
    repository({
      name: 'booking-race-e2e',
      profile: 'chaos-e2e',
      sources: [
        'hacker-house-medellin/hhm-api',
        'hacker-house-medellin/hhm-sync',
        'hacker-house-medellin/hhm-interfaces',
      ],
      focus: [
        'room bed and desk inventory races',
        'double-booking prevention',
        'lease expiry',
        'ambiguous retries',
        'crash between reservation and payment',
      ],
      services: ['postgres'],
      topic: 'chaos',
    }),
    repository({
      name: 'tenant-isolation-e2e',
      profile: 'security-e2e',
      sources: [
        'hacker-house-medellin/hhm-api',
        'hacker-house-medellin/hhm-interfaces',
      ],
      focus: [
        'resident operator and owner isolation',
        'cross-house object access refusal',
        'row-level security',
        'WebSocket subscription isolation',
        'audit evidence without private payloads',
      ],
      services: ['postgres'],
      topic: 'security',
    }),
    repository({
      name: 'payment-idempotency-e2e',
      profile: 'security-e2e',
      sources: [
        'hacker-house-medellin/hhm-api',
        'hacker-house-medellin/hhm-interfaces',
      ],
      focus: [
        'deposit invoice refund and receipt state machines',
        'idempotency keys',
        'webhook replay',
        'ambiguous timeout recovery',
        'synthetic payment data only',
      ],
      services: ['postgres'],
      topic: 'security',
    }),
    repository({
      name: 'access-control-lifecycle-e2e',
      profile: 'security-e2e',
      sources: [
        'hacker-house-medellin/hhm-api',
        'hacker-house-medellin/hhm-infra',
        'hacker-house-medellin/hhm-interfaces',
      ],
      focus: [
        'guest resident staff and vendor lifecycle',
        'short-lived access grants',
        'revocation and checkout',
        'replay and expiry refusal',
        'no real door credentials',
      ],
      services: ['postgres'],
      topic: 'security',
    }),
    repository({
      name: 'notification-reconnect-e2e',
      profile: 'protocol-e2e',
      sources: [
        'hacker-house-medellin/hhm-api',
        'hacker-house-medellin/hhm-sync',
        'hacker-house-medellin/hhm-interfaces',
      ],
      focus: [
        'notification ordering',
        'disconnect and resume',
        'duplicate suppression',
        'offline queue drain',
        'backpressure',
      ],
      topic: 'protocol',
    }),
    repository({
      name: 'performance-smoke-e2e',
      profile: 'performance-e2e',
      sources: [
        'hacker-house-medellin/hhm-api',
        'hacker-house-medellin/hhm-mash-web',
        'hacker-house-medellin/hhm-infra',
      ],
      focus: [
        'API latency budgets',
        'WebSocket fan-out',
        'booking contention',
        'SSR response budgets',
        'bounded synthetic occupancy',
      ],
      services: ['postgres'],
      topic: 'performance',
    }),
    repository({
      name: 'mcp-contract-e2e',
      profile: 'mcp-contract',
      sources: [
        'hacker-house-medellin/hhm-mcp-server.rs',
        'hacker-house-medellin/hhm-interfaces',
      ],
      focus: [
        'tool schema stability',
        'least-privilege actions',
        'tenant isolation',
        'prompt-injection-resistant resource handling',
        'synthetic fixtures only',
      ],
      imports: importsFor('hacker-house-medellin/hhm-interfaces', 'vendor/hhm-interfaces'),
      topic: 'mcp-contract',
    }),
    repository({
      name: 'clients-rust-consumer',
      profile: 'sdk-consumer',
      sources: [
        'hacker-house-medellin/hhm-clients',
        'hacker-house-medellin/hhm-interfaces',
      ],
      focus: [
        'Rust SDK API parity',
        'typed errors and WebSocket events',
        'offline and retry behavior',
        'Cargo packaging',
      ],
      imports: importsFor(
        'hacker-house-medellin/hhm-clients',
        'vendor/hhm-clients',
        { package: 'hhm-client', manager: 'cargo', subdir: 'clients/rust' },
      ),
      topic: 'sdk-consumer',
      matrix: { language: ['rust'] },
    }),
    repository({
      name: 'clients-typescript-consumer',
      profile: 'sdk-consumer',
      sources: [
        'hacker-house-medellin/hhm-clients',
        'hacker-house-medellin/hhm-interfaces',
      ],
      focus: [
        'TypeScript SDK API parity',
        'browser Node Deno Bun and edge exports',
        'typed errors and WebSocket events',
        'npm packaging',
      ],
      imports: importsFor(
        'hacker-house-medellin/hhm-clients',
        'vendor/hhm-clients',
        {
          package: '@hacker-house-medellin/hhm-client',
          manager: 'npm',
          subdir: 'clients/typescript',
        },
      ),
      topic: 'sdk-consumer',
      matrix: { language: ['typescript'] },
    }),
  ],
};

function readManifest() {
  const encoded = fs
    .readdirSync(partsDir)
    .sort()
    .map((name) => fs.readFileSync(path.join(partsDir, name), 'utf8').trim())
    .join('');
  return JSON.parse(zlib.gunzipSync(Buffer.from(encoded, 'base64')).toString('utf8'));
}

function writeManifest(manifest) {
  const serialized = `${JSON.stringify(manifest, null, 2)}\n`;
  const compressed = zlib.gzipSync(Buffer.from(serialized), {
    level: zlib.constants.Z_BEST_COMPRESSION,
    mtime: 0,
  });
  const encoded = compressed.toString('base64');
  const chunks = encoded.match(/.{1,4800}/g) ?? [];
  for (const name of fs.readdirSync(partsDir)) fs.rmSync(path.join(partsDir, name));
  chunks.forEach((chunk, index) => {
    fs.writeFileSync(path.join(partsDir, `${String(index).padStart(3, '0')}.b64.part`), `${chunk}\n`);
  });
}

function replaceRequired(file, pattern, replacement, description) {
  const filePath = path.join(root, file);
  const before = fs.readFileSync(filePath, 'utf8');
  const after = before.replace(pattern, replacement);
  if (after === before) throw new Error(`could not update ${description} in ${file}`);
  fs.writeFileSync(filePath, after);
}

function addOrganizationToWorkflow(file) {
  const filePath = path.join(root, file);
  let content = fs.readFileSync(filePath, 'utf8');
  if (content.includes(targetOrg)) return;
  if (file.endsWith('apply-test-org-fleet.yml')) {
    content = content
      .replace('          - streempilot-test\n', `          - ${targetOrg}\n          - streempilot-test\n`)
      .replace(
        '"hypesiege-test","streempilot-test"]',
        `"hypesiege-test","${targetOrg}","streempilot-test"]`,
      )
      .replace(
        '|hypesiege-test|streempilot-test)',
        `|hypesiege-test|${targetOrg}|streempilot-test)`,
      );
  } else {
    content = content.replace(
      '            streempilot-test\n',
      `            ${targetOrg}\n            streempilot-test\n`,
    );
  }
  if (!content.includes(targetOrg)) throw new Error(`could not add ${targetOrg} to ${file}`);
  fs.writeFileSync(filePath, content);
}

const manifest = readManifest();
if (!manifest.pairs.some((pair) => pair.testOrg === targetOrg)) {
  manifest.pairs.push(hhmPair);
}
const pair = manifest.pairs.find((candidate) => candidate.testOrg === targetOrg);
if (pair.repositories.length !== 18) throw new Error('HHM must define exactly 18 specialized repositories');
if (new Set(pair.repositories.map((item) => item.name)).size !== pair.repositories.length) {
  throw new Error('HHM repository names must be unique');
}
writeManifest(manifest);

replaceRequired(
  'scripts/export-test-org-fleet-index.mjs',
  /if \(result\.pairCount !== 18\) throw new Error\(`expected 18 pairs, got \$\{result\.pairCount\}`\);/,
  'if (result.pairCount !== 19) throw new Error(`expected 19 pairs, got ${result.pairCount}`);',
  'export pair count',
);
replaceRequired(
  'scripts/export-test-org-fleet-index.mjs',
  /if \(result\.specializedRepositoryCount !== 301\) throw new Error\(`expected 301 specialized repositories, got \$\{result\.specializedRepositoryCount\}`\);/,
  'if (result.specializedRepositoryCount !== 319) throw new Error(`expected 319 specialized repositories, got ${result.specializedRepositoryCount}`);',
  'export specialized count',
);
replaceRequired(
  'scripts/export-test-org-fleet-index.mjs',
  /if \(result\.governanceRepositoryCount !== 18\) throw new Error\(`expected 18 governance repositories, got \$\{result\.governanceRepositoryCount\}`\);/,
  'if (result.governanceRepositoryCount !== 19) throw new Error(`expected 19 governance repositories, got ${result.governanceRepositoryCount}`);',
  'export governance count',
);
replaceRequired(
  'scripts/export-test-org-fleet-index.mjs',
  /if \(result\.managedRepositoryCount !== 319\) throw new Error\(`expected 319 managed repositories, got \$\{result\.managedRepositoryCount\}`\);/,
  'if (result.managedRepositoryCount !== 338) throw new Error(`expected 338 managed repositories, got ${result.managedRepositoryCount}`);',
  'export managed count',
);
replaceRequired(
  'scripts/export-test-org-fleet-index.mjs',
  /if \(result\.expectedRepositoryCount !== 341\) throw new Error\(`expected 341 total repositories, got \$\{result\.expectedRepositoryCount\}`\);/,
  'if (result.expectedRepositoryCount !== 360) throw new Error(`expected 360 total repositories, got ${result.expectedRepositoryCount}`);',
  'export expected count',
);

replaceRequired(
  'docs/test-org-fleet-index.md',
  /- 18 production\/test pairs;/,
  '- 19 production/test pairs;',
  'documentation pair count',
);
replaceRequired(
  'docs/test-org-fleet-index.md',
  /- 301 specialized repositories;/,
  '- 319 specialized repositories;',
  'documentation specialized count',
);
replaceRequired(
  'docs/test-org-fleet-index.md',
  /- 18 governance repositories;/,
  '- 19 governance repositories;',
  'documentation governance count',
);
replaceRequired(
  'docs/test-org-fleet-index.md',
  /- 319 managed repositories;/,
  '- 338 managed repositories;',
  'documentation managed count',
);
replaceRequired(
  'docs/test-org-fleet-index.md',
  /- 341 total expected repositories;/,
  '- 360 total expected repositories;',
  'documentation expected count',
);

replaceRequired(
  'tests/test-org-fleet.test.mjs',
  /assert\.equal\(manifest\.pairs\.length, 18\);/,
  'assert.equal(manifest.pairs.length, 19);',
  'test manifest pair count',
);
replaceRequired(
  'tests/test-org-fleet.test.mjs',
  /assert\.equal\(summary\.pairs, 18\);/g,
  'assert.equal(summary.pairs, 19);',
  'test summary pair count',
);
replaceRequired(
  'tests/test-org-fleet.test.mjs',
  /assert\.equal\(summary\.createRepositories, 301\);/g,
  'assert.equal(summary.createRepositories, 319);',
  'test create count',
);
replaceRequired(
  'tests/test-org-fleet.test.mjs',
  /assert\.equal\(summary\.governanceRepositories, 18\);/g,
  'assert.equal(summary.governanceRepositories, 19);',
  'test governance count',
);
replaceRequired(
  'tests/test-org-fleet.test.mjs',
  /assert\.equal\(summary\.selectedOrganizations, 18\);/,
  'assert.equal(summary.selectedOrganizations, 19);',
  'test selected organization count',
);
replaceRequired(
  'tests/test-org-fleet.test.mjs',
  /assert\.equal\(summary\.selectedRepositories, 301\);/,
  'assert.equal(summary.selectedRepositories, 319);',
  'test selected repository count',
);
replaceRequired(
  'tests/test-org-fleet.test.mjs',
  /assert\.equal\(summary\.planned, 319\);/,
  'assert.equal(summary.planned, 338);',
  'test planned count',
);

addOrganizationToWorkflow('.github/workflows/apply-test-org-fleet.yml');
addOrganizationToWorkflow('.github/workflows/secure-fleet-token-handoff.yml');

const hhmTest = `\n\ntest('Hacker House Medellín uses synthetic, privacy-safe product coverage', () => {
  const pair = manifest.pairs.find((candidate) => candidate.testOrg === '${targetOrg}');
  assert.ok(pair);
  assert.equal(pair.sourceOrg, 'hacker-house-medellin');
  assert.equal(pair.defaultVisibility, 'private');
  assert.equal(pair.repositories.length, 18);
  const names = new Set(pair.repositories.map((repository) => repository.name));
  for (const required of [
    'mash-web-e2e',
    'leptos-web-e2e',
    'dioxus-web-e2e',
    'api-contract-e2e',
    'websocket-contract-e2e',
    'cli-contract-e2e',
    'sync-offline-e2e',
    'infra-compose-e2e',
    'postgres-supabase-e2e',
    'booking-race-e2e',
    'tenant-isolation-e2e',
    'payment-idempotency-e2e',
    'access-control-lifecycle-e2e',
    'notification-reconnect-e2e',
    'performance-smoke-e2e',
    'mcp-contract-e2e',
    'clients-rust-consumer',
    'clients-typescript-consumer',
  ]) assert.equal(names.has(required), true, required);
  const serializedPair = JSON.stringify(pair);
  assert.equal(credentialLiteral.test(serializedPair), false);
  assert.doesNotMatch(serializedPair, /(?:resident identity document|door credential|payment card|private message|real occupancy record)/i);
  for (const repository of pair.repositories) {
    assert.equal(repository.sources.every((source) => /^[^/]+\\/[^/]+$/.test(source)), true);
    assert.equal(repository.focus.length >= 3, true);
  }
});
`;
const testsPath = path.join(root, 'tests', 'test-org-fleet.test.mjs');
let tests = fs.readFileSync(testsPath, 'utf8');
if (!tests.includes('Hacker House Medellín uses synthetic')) {
  tests += hhmTest;
  fs.writeFileSync(testsPath, tests);
}

console.log(JSON.stringify({
  testOrg: targetOrg,
  specializedRepositories: pair.repositories.length,
  pairCount: manifest.pairs.length,
  expectedRepositoryCount: 360,
}, null, 2));
