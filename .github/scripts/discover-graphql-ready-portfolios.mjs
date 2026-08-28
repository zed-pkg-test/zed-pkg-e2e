import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { spawnSync } from 'node:child_process';

const token = process.env.FLEET_GH_TOKEN;
if (!token) throw new Error('FLEET_GH_TOKEN is required');

const output = path.resolve('graphql-portfolio-bundle');
const archives = path.join(output, 'archives');
fs.rmSync(output, { recursive: true, force: true });
fs.mkdirSync(archives, { recursive: true });

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function graphql(query, variables = {}, attempts = 5) {
  let last;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const response = await fetch('https://api.github.com/graphql', {
      method: 'POST',
      headers: {
        Accept: 'application/vnd.github+json',
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
        'User-Agent': 'graphql-ready-portfolio-discovery',
      },
      body: JSON.stringify({ query, variables }),
    });
    const value = await response.json().catch(() => null);
    if (response.ok && !value?.errors?.length) return value.data;
    last = new Error(`GraphQL failed: ${JSON.stringify(value).slice(0, 1800)}`);
    if (attempt === attempts) throw last;
    await sleep(attempt * attempt * 900);
  }
  throw last;
}

const query = `
  query ReadyPortfolios($search: String!) {
    viewer { login }
    search(query: $search, type: ISSUE, first: 100) {
      issueCount
      nodes {
        ... on PullRequest {
          id
          number
          title
          body
          createdAt
          state
          isDraft
          headRefName
          headRefOid
          baseRefName
          mergeable
          mergeStateStatus
          comments(first: 100) { totalCount pageInfo { hasNextPage } nodes { body } }
          reviewThreads(first: 100) { pageInfo { hasNextPage } nodes { isResolved } }
          reviews(last: 100) { pageInfo { hasPreviousPage } nodes { state author { login } } }
          labels(first: 50) { nodes { name } }
          files(first: 100) { totalCount pageInfo { hasNextPage } nodes { path changeType } }
          repository {
            nameWithOwner
            isPrivate
            isArchived
            viewerPermission
            defaultBranchRef { name }
            pullRequests(states: OPEN, first: 100) {
              pageInfo { hasNextPage }
              nodes {
                number
                headRefOid
                files(first: 100) { pageInfo { hasNextPage } nodes { path } }
              }
            }
          }
        }
      }
    }
    rateLimit { remaining resetAt cost }
  }
`;

const search = 'is:pr is:open author:ORESoftware draft:true created:<2026-08-05T12:45:00Z "Declared readiness: `ready`"';
const data = await graphql(query, { search });
if (data.viewer.login !== 'ORESoftware') throw new Error(`unexpected viewer ${data.viewer.login}`);

const vetoPattern = /do not merge|don['’]?t merge|never merge|temporary synchronization|\bwip\b|work in progress|not ready|hold (?:this|off)|blocked by|changes requested|must not merge/i;
const latestReviewStates = (pr) => {
  const latest = new Map();
  for (const review of pr.reviews?.nodes || []) {
    if (!review.author?.login || ['COMMENTED', 'PENDING', 'DISMISSED'].includes(review.state)) continue;
    latest.set(review.author.login, review.state);
  }
  return latest;
};
const intersects = (left, right) => {
  for (const value of left) if (right.has(value)) return true;
  return false;
};

const inspected = [];
for (const pr of data.search.nodes || []) {
  if (!pr?.repository?.nameWithOwner) continue;
  const repo = pr.repository.nameWithOwner;
  const [owner] = repo.split('/');
  const comments = (pr.comments?.nodes || []).map((comment) => comment.body || '').join('\n');
  const labels = (pr.labels?.nodes || []).map((label) => label.name).join(' ');
  const reasons = [];
  if (!owner.toLowerCase().endsWith('-test')) reasons.push('not-test-org');
  if (pr.title !== 'test: bootstrap independent acceptance portfolio') reasons.push('wrong-title');
  if (!/- Declared readiness:\s*`ready`/i.test(pr.body || '')) reasons.push('not-declared-ready');
  if (/planned_dependency|source-gated until:/i.test(pr.body || '')) reasons.push('planned-or-source-gated');
  if (vetoPattern.test(`${pr.title}\n${pr.body || ''}\n${comments}\n${labels}`)) reasons.push('veto');
  if (pr.state !== 'OPEN' || !pr.isDraft) reasons.push('not-open-draft');
  if (pr.repository.isPrivate) reasons.push('private-repository');
  if (pr.repository.isArchived || !['ADMIN', 'MAINTAIN', 'WRITE'].includes(pr.repository.viewerPermission)) reasons.push('permission');
  if (pr.baseRefName !== pr.repository.defaultBranchRef?.name) reasons.push('non-default-base');
  if (pr.mergeable === 'CONFLICTING' || ['DIRTY', 'BLOCKED', 'UNKNOWN'].includes(pr.mergeStateStatus)) reasons.push(`merge-${pr.mergeable}-${pr.mergeStateStatus}`);
  if (pr.comments?.totalCount !== 0 || pr.comments?.pageInfo?.hasNextPage) reasons.push('comments');
  if (pr.reviewThreads?.pageInfo?.hasNextPage || (pr.reviewThreads?.nodes || []).some((thread) => !thread.isResolved)) reasons.push('review-thread');
  if (pr.reviews?.pageInfo?.hasPreviousPage || [...latestReviewStates(pr).values()].includes('CHANGES_REQUESTED')) reasons.push('changes-requested');
  if (pr.files?.pageInfo?.hasNextPage || pr.files?.totalCount < 1 || pr.files?.totalCount > 100) reasons.push('file-count');
  if ((pr.files?.nodes || []).some((file) => ['DELETED', 'RENAMED'].includes(file.changeType))) reasons.push('destructive-file-change');
  if (pr.repository.pullRequests?.pageInfo?.hasNextPage) reasons.push('too-many-open-prs');

  if (!reasons.length) {
    const mine = new Set((pr.files?.nodes || []).map((file) => file.path));
    for (const other of pr.repository.pullRequests?.nodes || []) {
      if (other.number === pr.number) continue;
      if (other.files?.pageInfo?.hasNextPage) {
        reasons.push('unbounded-other-pr-files');
        break;
      }
      if (intersects(mine, new Set((other.files?.nodes || []).map((file) => file.path)))) {
        reasons.push(`overlap-with-pr-${other.number}`);
        break;
      }
    }
  }
  inspected.push({ pr, reasons: [...new Set(reasons)] });
}

const eligible = inspected.filter((entry) => entry.reasons.length === 0).sort((a, b) => String(a.pr.createdAt).localeCompare(String(b.pr.createdAt)));
const selected = [];
const repos = new Set();
for (const entry of eligible) {
  const repo = entry.pr.repository.nameWithOwner;
  if (repos.has(repo)) continue;
  repos.add(repo);
  selected.push(entry.pr);
}

const sanitizedEnv = { ...process.env };
delete sanitizedEnv.FLEET_GH_TOKEN;
delete sanitizedEnv.GH_TOKEN;
delete sanitizedEnv.GITHUB_TOKEN;
sanitizedEnv.GIT_CONFIG_COUNT = '1';
sanitizedEnv.GIT_CONFIG_KEY_0 = 'core.hooksPath';
sanitizedEnv.GIT_CONFIG_VALUE_0 = '/dev/null';
sanitizedEnv.GIT_TERMINAL_PROMPT = '0';

const manifest = [];
const cloneFailures = [];
for (let index = 0; index < selected.length; index += 1) {
  const pr = selected[index];
  const repo = pr.repository.nameWithOwner;
  const repoName = repo.split('/')[1];
  const work = fs.mkdtempSync(path.join(process.env.RUNNER_TEMP || '/tmp', 'portfolio-clone-'));
  const clone = path.join(work, 'repo');
  try {
    const cloneResult = spawnSync('git', ['clone', '--no-checkout', '--filter=blob:none', '--single-branch', '--branch', pr.headRefName, `https://github.com/${repo}.git`, clone], {
      env: sanitizedEnv,
      encoding: 'utf8',
      timeout: 180000,
      maxBuffer: 8 * 1024 * 1024,
    });
    if (cloneResult.status !== 0) throw new Error(`clone failed: ${(cloneResult.stdout || '')}${(cloneResult.stderr || '')}`);
    const rev = spawnSync('git', ['-C', clone, 'rev-parse', `refs/remotes/origin/${pr.headRefName}`], { env: sanitizedEnv, encoding: 'utf8', timeout: 30000 });
    if (rev.status !== 0 || rev.stdout.trim() !== pr.headRefOid) throw new Error(`cloned head mismatch: ${rev.stdout.trim()} != ${pr.headRefOid}`);
    const filename = `${String(index).padStart(3, '0')}-${repo.replace('/', '__')}--pr-${pr.number}.tar.gz`;
    const archivePath = path.join(archives, filename);
    const file = fs.openSync(archivePath, 'w', 0o600);
    const archive = spawnSync('git', ['-C', clone, 'archive', '--format=tar.gz', `--prefix=${repoName}/`, pr.headRefOid], {
      env: sanitizedEnv,
      stdio: ['ignore', file, 'pipe'],
      timeout: 120000,
      maxBuffer: 8 * 1024 * 1024,
    });
    fs.closeSync(file);
    if (archive.status !== 0) throw new Error(`archive failed: ${String(archive.stderr)}`);
    const bytes = fs.readFileSync(archivePath);
    if (bytes.length < 256) throw new Error('archive too small');
    manifest.push({
      repo,
      number: pr.number,
      node_id: pr.id,
      title: pr.title,
      head_ref: pr.headRefName,
      head_sha: pr.headRefOid,
      base_ref: pr.baseRefName,
      archive: `archives/${filename}`,
      archive_sha256: crypto.createHash('sha256').update(bytes).digest('hex'),
      archive_bytes: bytes.length,
    });
  } catch (error) {
    cloneFailures.push({ repo, number: pr.number, error: error.message.slice(0, 2000) });
  } finally {
    fs.rmSync(work, { recursive: true, force: true });
  }
}

const skipCounts = new Map();
for (const entry of inspected) for (const reason of entry.reasons) skipCounts.set(reason, (skipCounts.get(reason) || 0) + 1);
const document = {
  schema_version: 1,
  generated_at: new Date().toISOString(),
  search_count: data.search.issueCount,
  selected_count: selected.length,
  entries: manifest,
  clone_failures: cloneFailures,
};
fs.writeFileSync(path.join(output, 'manifest.json'), JSON.stringify(document, null, 2) + '\n');
console.log(`GRAPHQL_PORTFOLIO_DISCOVERY ${JSON.stringify({ searched: data.search.issueCount, inspected: inspected.length, eligible: eligible.length, selected: selected.length, archived: manifest.length, clone_failures: cloneFailures.length, graphql_remaining: data.rateLimit.remaining })}`);
for (const [reason, count] of [...skipCounts.entries()].sort((a, b) => b[1] - a[1])) console.log(`SKIP ${count}\t${reason}`);
for (const failure of cloneFailures) console.log(`CLONE_FAILURE ${JSON.stringify(failure)}`);
if (!manifest.length) throw new Error('no ready portfolio archives were produced');
