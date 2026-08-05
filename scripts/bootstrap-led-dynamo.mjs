import process from 'node:process';
import { readdir, readFile } from 'node:fs/promises';
import { gunzipSync } from 'node:zlib';

const OWNER = 'led-dynamo';
const CONFIRMATION = process.env.LEDDY_BOOTSTRAP_CONFIRM;
const TOKEN = process.env.GH_TOKEN;
const PARTS_DIR = new URL('../bootstrap/led-dynamo.parts/', import.meta.url);

if (CONFIRMATION !== 'CREATE_LEDDY_REPOSITORIES') {
  throw new Error('LEDDY_BOOTSTRAP_CONFIRM must be CREATE_LEDDY_REPOSITORIES');
}
if (!TOKEN) throw new Error('GH_TOKEN is required');

const partNames = (await readdir(PARTS_DIR)).filter((name) => name.endsWith('.b64')).sort();
if (partNames.length === 0) throw new Error('bootstrap archive parts are missing');
const archive = (await Promise.all(partNames.map((name) => readFile(new URL(name, PARTS_DIR), 'utf8')))).join('').replace(/\s/g, '');
const repositories = JSON.parse(gunzipSync(Buffer.from(archive, 'base64')).toString('utf8'));

const headers = {
  accept: 'application/vnd.github+json',
  authorization: `Bearer ${TOKEN}`,
  'content-type': 'application/json',
  'user-agent': 'leddy-repository-bootstrap',
  'x-github-api-version': '2022-11-28',
};

async function api(method, path, body, allowed = []) {
  const response = await fetch(`https://api.github.com${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 204) return null;
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok && !allowed.includes(response.status)) {
    const message = data?.message ?? text ?? response.statusText;
    throw new Error(`${method} ${path} -> ${response.status}: ${message}`);
  }
  return { status: response.status, data };
}

function encodedPath(path) {
  return path.split('/').map(encodeURIComponent).join('/');
}

async function ensureRepository(spec) {
  const path = `/repos/${OWNER}/${encodeURIComponent(spec.name)}`;
  const current = await api('GET', path, undefined, [404]);
  if (current.status === 404) {
    await api('POST', `/orgs/${OWNER}/repos`, {
      name: spec.name,
      description: spec.description,
      homepage: '',
      private: false,
      has_issues: true,
      has_projects: true,
      has_wiki: false,
      has_discussions: true,
      auto_init: false,
    });
    console.log(`created ${OWNER}/${spec.name}`);
  } else {
    console.log(`adopting ${OWNER}/${spec.name}`);
  }

  await api('PATCH', path, {
    description: spec.description,
    has_issues: true,
    has_projects: true,
    has_wiki: false,
    has_discussions: true,
    allow_squash_merge: true,
    allow_merge_commit: true,
    allow_rebase_merge: true,
    delete_branch_on_merge: true,
  });
  await api('PUT', `${path}/topics`, { names: spec.topics.slice(0, 20) });
}

async function branchHead(repo, branch) {
  const result = await api(
    'GET',
    `/repos/${OWNER}/${encodeURIComponent(repo)}/git/ref/heads/${encodeURIComponent(branch)}`,
    undefined,
    [404, 409],
  );
  return [404, 409].includes(result.status) ? null : result.data.object.sha;
}

async function createInitialCommit(spec) {
  const repoPath = `/repos/${OWNER}/${encodeURIComponent(spec.name)}`;
  const entries = [];
  for (const [path, content] of Object.entries(spec.files)) {
    const blob = await api('POST', `${repoPath}/git/blobs`, { content, encoding: 'utf-8' });
    entries.push({ path, mode: '100644', type: 'blob', sha: blob.data.sha });
  }
  const tree = await api('POST', `${repoPath}/git/trees`, { tree: entries });
  const commit = await api('POST', `${repoPath}/git/commits`, {
    message: 'chore: bootstrap Leddy repository',
    tree: tree.data.sha,
    parents: [],
  });
  await api('POST', `${repoPath}/git/refs`, { ref: 'refs/heads/main', sha: commit.data.sha });
  return commit.data.sha;
}

async function ensureFile(repo, path, content) {
  const repoPath = `/repos/${OWNER}/${encodeURIComponent(repo)}`;
  const filePath = `${repoPath}/contents/${encodedPath(path)}`;
  const current = await api('GET', `${filePath}?ref=main`, undefined, [404]);
  if (current.status !== 404) {
    const existing = Buffer.from(current.data.content.replace(/\n/g, ''), 'base64').toString('utf8');
    if (existing === content) return false;
  }
  await api('PUT', filePath, {
    message: `chore: reconcile ${path}`,
    content: Buffer.from(content, 'utf8').toString('base64'),
    branch: 'main',
    ...(current.status === 404 ? {} : { sha: current.data.sha }),
  });
  return true;
}

async function seedRepository(spec) {
  let mainSha = await branchHead(spec.name, 'main');
  if (!mainSha) {
    mainSha = await createInitialCommit(spec);
    console.log(`seeded ${OWNER}/${spec.name} at ${mainSha.slice(0, 12)}`);
  } else {
    let changed = 0;
    for (const [path, content] of Object.entries(spec.files)) {
      if (await ensureFile(spec.name, path, content)) changed += 1;
    }
    mainSha = await branchHead(spec.name, 'main');
    console.log(`reconciled ${OWNER}/${spec.name} (${changed} files changed)`);
  }

  if (!(await branchHead(spec.name, 'dev'))) {
    await api('POST', `/repos/${OWNER}/${encodeURIComponent(spec.name)}/git/refs`, {
      ref: 'refs/heads/dev',
      sha: mainSha,
    });
    console.log(`created ${OWNER}/${spec.name} dev branch`);
  }
}

await api('GET', `/orgs/${OWNER}`);
console.log(`verified organization ${OWNER}`);

for (const spec of repositories) await ensureRepository(spec);
for (const spec of repositories) await seedRepository(spec);

console.log(JSON.stringify({
  organization: OWNER,
  repositories: repositories.map((repository) => repository.name),
  count: repositories.length,
  zedPackages: ['leddy-interfaces', 'leddy-lib', 'leddy-clients'],
  lockfiles: 'resolver-generated only; none fabricated',
}, null, 2));
