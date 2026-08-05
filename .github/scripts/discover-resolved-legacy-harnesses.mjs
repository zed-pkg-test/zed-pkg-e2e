import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { spawnSync } from 'node:child_process';

const token = process.env.FLEET_GH_TOKEN;
if (!token) throw new Error('FLEET_GH_TOKEN is required');
const output = path.resolve('legacy-harness-bundle');
const archives = path.join(output, 'archives');
fs.rmSync(output, { recursive: true, force: true });
fs.mkdirSync(archives, { recursive: true });

async function graphql(query, variables = {}) {
  const response = await fetch('https://api.github.com/graphql', {
    method: 'POST',
    headers: { Accept: 'application/vnd.github+json', Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', 'User-Agent': 'resolved-legacy-harness-discovery' },
    body: JSON.stringify({ query, variables }),
  });
  const value = await response.json();
  if (!response.ok || !value.data) throw new Error(JSON.stringify(value).slice(0, 1800));
  return value.data;
}

const query = `
  query Harnesses($search: String!) {
    viewer { login }
    search(query: $search, type: ISSUE, first: 100) {
      issueCount
      nodes {
        ... on PullRequest {
          id number title body state isDraft headRefName headRefOid baseRefName mergeable mergeStateStatus
          comments(first: 100) { totalCount pageInfo { hasNextPage } nodes { body } }
          reviewThreads(first: 100) { pageInfo { hasNextPage } nodes { isResolved } }
          reviews(last: 100) { pageInfo { hasPreviousPage } nodes { state author { login } } }
          files(first: 100) { totalCount pageInfo { hasNextPage } nodes { path changeType } }
          labels(first: 50) { nodes { name } }
          repository { nameWithOwner isPrivate isArchived viewerPermission defaultBranchRef { name } pullRequests(states: OPEN, first: 1) { totalCount } }
        }
      }
    }
    rateLimit { remaining cost resetAt }
  }
`;
const search = 'is:pr is:open author:ORESoftware draft:true created:<2026-08-05T12:45:00Z "All declared source repositories resolved at bootstrap time"';
const data = await graphql(query, { search });
if (data.viewer.login !== 'ORESoftware') throw new Error(`unexpected viewer ${data.viewer.login}`);
const veto = /do not merge|don['’]?t merge|never merge|\bwip\b|work in progress|not ready|blocked by|changes requested|must not merge/i;
const latest = (pr) => {
  const map = new Map();
  for (const review of pr.reviews?.nodes || []) {
    if (!review.author?.login || ['COMMENTED', 'PENDING', 'DISMISSED'].includes(review.state)) continue;
    map.set(review.author.login, review.state);
  }
  return map;
};
const selected = [];
const skipped = [];
for (const pr of data.search.nodes || []) {
  if (!pr?.repository?.nameWithOwner) continue;
  const repo = pr.repository.nameWithOwner;
  const [owner] = repo.split('/');
  const comments = (pr.comments?.nodes || []).map((comment) => comment.body || '').join('\n');
  const labels = (pr.labels?.nodes || []).map((label) => label.name).join(' ');
  const reasons = [];
  if (!owner.toLowerCase().endsWith('-test')) reasons.push('not-test-org');
  if (!/^test: bootstrap [a-z0-9][a-z0-9-]* harness$/i.test(pr.title || '')) reasons.push('wrong-title');
  if (!/All declared source repositories resolved at bootstrap time/i.test(pr.body || '')) reasons.push('not-source-resolved');
  if (pr.state !== 'OPEN' || !pr.isDraft) reasons.push('not-open-draft');
  if (pr.repository.isPrivate || pr.repository.isArchived || !['ADMIN', 'MAINTAIN', 'WRITE'].includes(pr.repository.viewerPermission)) reasons.push('repository-gate');
  if (pr.baseRefName !== pr.repository.defaultBranchRef?.name) reasons.push('non-default-base');
  if (pr.mergeable === 'CONFLICTING' || ['DIRTY', 'BLOCKED', 'UNKNOWN'].includes(pr.mergeStateStatus)) reasons.push(`merge-${pr.mergeable}-${pr.mergeStateStatus}`);
  if (pr.comments?.totalCount !== 0 || pr.comments?.pageInfo?.hasNextPage) reasons.push('comments');
  if (pr.reviewThreads?.pageInfo?.hasNextPage || (pr.reviewThreads?.nodes || []).some((thread) => !thread.isResolved)) reasons.push('review-thread');
  if (pr.reviews?.pageInfo?.hasPreviousPage || [...latest(pr).values()].includes('CHANGES_REQUESTED')) reasons.push('changes-requested');
  if (pr.files?.pageInfo?.hasNextPage || pr.files?.totalCount < 1 || pr.files?.totalCount > 100) reasons.push('file-count');
  if ((pr.files?.nodes || []).some((file) => ['DELETED', 'RENAMED'].includes(file.changeType))) reasons.push('destructive-change');
  if (pr.repository.pullRequests?.totalCount !== 1) reasons.push(`open-pr-count-${pr.repository.pullRequests?.totalCount}`);
  if (veto.test(`${pr.title}\n${pr.body || ''}\n${comments}\n${labels}`)) reasons.push('veto');
  if (reasons.length) skipped.push({ repo, number: pr.number, reasons });
  else selected.push(pr);
}

const cleanEnv = { ...process.env };
for (const name of ['FLEET_GH_TOKEN', 'GH_TOKEN', 'GITHUB_TOKEN']) delete cleanEnv[name];
cleanEnv.GIT_TERMINAL_PROMPT = '0';
cleanEnv.GIT_CONFIG_COUNT = '1';
cleanEnv.GIT_CONFIG_KEY_0 = 'core.hooksPath';
cleanEnv.GIT_CONFIG_VALUE_0 = '/dev/null';
const entries = [];
const failures = [];
for (let index = 0; index < selected.length; index += 1) {
  const pr = selected[index];
  const repo = pr.repository.nameWithOwner;
  const repoName = repo.split('/')[1];
  const work = fs.mkdtempSync(path.join(process.env.RUNNER_TEMP || '/tmp', 'legacy-harness-'));
  try {
    const clone = path.join(work, 'repo');
    const result = spawnSync('git', ['clone', '--no-checkout', '--filter=blob:none', '--single-branch', '--branch', pr.headRefName, `https://github.com/${repo}.git`, clone], { env: cleanEnv, encoding: 'utf8', timeout: 180000, maxBuffer: 8 * 1024 * 1024 });
    if (result.status !== 0) throw new Error(`clone failed: ${(result.stdout || '')}${(result.stderr || '')}`);
    const rev = spawnSync('git', ['-C', clone, 'rev-parse', `refs/remotes/origin/${pr.headRefName}`], { env: cleanEnv, encoding: 'utf8', timeout: 30000 });
    if (rev.status !== 0 || rev.stdout.trim() !== pr.headRefOid) throw new Error(`head mismatch ${rev.stdout.trim()} != ${pr.headRefOid}`);
    const filename = `${String(index).padStart(3, '0')}-${repo.replace('/', '__')}--pr-${pr.number}.tar.gz`;
    const archivePath = path.join(archives, filename);
    const fd = fs.openSync(archivePath, 'w', 0o600);
    const archived = spawnSync('git', ['-C', clone, 'archive', '--format=tar.gz', `--prefix=${repoName}/`, pr.headRefOid], { env: cleanEnv, stdio: ['ignore', fd, 'pipe'], timeout: 120000, maxBuffer: 8 * 1024 * 1024 });
    fs.closeSync(fd);
    if (archived.status !== 0) throw new Error(`archive failed: ${String(archived.stderr)}`);
    const bytes = fs.readFileSync(archivePath);
    entries.push({ repo, number: pr.number, node_id: pr.id, title: pr.title, head_ref: pr.headRefName, head_sha: pr.headRefOid, base_ref: pr.baseRefName, archive: `archives/${filename}`, archive_sha256: crypto.createHash('sha256').update(bytes).digest('hex'), archive_bytes: bytes.length });
  } catch (error) {
    failures.push({ repo, number: pr.number, error: error.message.slice(0, 2000) });
  } finally {
    fs.rmSync(work, { recursive: true, force: true });
  }
}
fs.writeFileSync(path.join(output, 'manifest.json'), JSON.stringify({ schema_version: 1, generated_at: new Date().toISOString(), search_count: data.search.issueCount, entries, skipped, failures }, null, 2) + '\n');
console.log(`LEGACY_HARNESS_DISCOVERY ${JSON.stringify({ searched: data.search.issueCount, selected: selected.length, archived: entries.length, skipped: skipped.length, failures: failures.length, graphql_remaining: data.rateLimit.remaining })}`);
for (const item of skipped) console.log(`SKIP ${item.repo}#${item.number} ${item.reasons.join(',')}`);
for (const item of failures) console.log(`FAILURE ${JSON.stringify(item)}`);
if (!entries.length) throw new Error('no resolved legacy harnesses archived');
