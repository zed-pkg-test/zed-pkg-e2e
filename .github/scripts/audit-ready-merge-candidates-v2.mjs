import fs from 'node:fs';

const token = process.env.GH_TOKEN;
if (!token) throw new Error('GH_TOKEN is required');

const cutoff = '2026-08-05T12:45:00Z';
const api = 'https://api.github.com';
const headers = {
  Accept: 'application/vnd.github+json',
  Authorization: `Bearer ${token}`,
  'X-GitHub-Api-Version': '2022-11-28',
  'User-Agent': 'comment-aware-ready-pr-auditor',
};
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function request(url, attempts = 6) {
  let last;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const response = await fetch(url, { headers });
    const text = await response.text();
    let value;
    try { value = text ? JSON.parse(text) : null; } catch { value = text; }
    if (response.ok) return value;
    last = new Error(`GET ${url} -> ${response.status}: ${String(text).slice(0, 600)}`);
    const retryable = response.status === 429 || response.status >= 500 ||
      (response.status === 403 && /secondary rate limit|abuse detection/i.test(text));
    if (!retryable || attempt === attempts) throw last;
    await sleep(attempt * attempt * 700);
  }
  throw last;
}

async function mapLimit(values, limit, fn) {
  const results = new Array(values.length);
  let next = 0;
  async function worker() {
    while (true) {
      const index = next++;
      if (index >= values.length) return;
      try { results[index] = await fn(values[index]); }
      catch (error) { results[index] = { audit_error: error.message, source: values[index] }; }
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, values.length) }, worker));
  return results;
}

const vetoPattern = /do not merge|don['’]?t merge|never merge|temporary synchronization|\bwip\b|work in progress|not ready|hold (?:this|off)|blocked by|changes requested|must not merge/i;
const concernPattern = /(?:still|currently) fail(?:ing|s)?|unresolved|needs? (?:a |an |the )?(?:fix|change|update|review)|regression remains|unsafe to merge/i;

function addedLines(patch) {
  return String(patch || '')
    .split('\n')
    .filter((line) => line.startsWith('+') && !line.startsWith('+++'))
    .map((line) => line.slice(1))
    .join('\n');
}

function sensitivePath(pathname) {
  const path = pathname.toLowerCase();
  return /(^|\/)(id_rsa|id_dsa|id_ecdsa|id_ed25519)(\.|$)/.test(path) ||
    /\.(pem|p12|pfx|jks|keystore|key)$/.test(path) ||
    /(^|\/)(credentials?|secrets?|tokens?)(\.|\/|$)/.test(path) ||
    (/(^|\/)\.env($|\.)/.test(path) && !/\.example$|\.sample$|\.template$/.test(path));
}

function patchReasons(files) {
  const reasons = [];
  for (const file of files) {
    if (sensitivePath(file.filename)) reasons.push(`sensitive-path:${file.filename}`);
    const patch = String(file.patch || '');
    const additions = addedLines(patch);
    if (file.patch == null && file.status !== 'removed') reasons.push(`unreviewable-patch:${file.filename}`);
    if (/^(<<<<<<<|=======|>>>>>>>)( |$)/m.test(patch)) reasons.push(`conflict-marker:${file.filename}`);
    if (/(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|xox[baprs]-[A-Za-z0-9-]{10,})/.test(additions)) {
      reasons.push(`credential-pattern:${file.filename}`);
    }
    if (/\.github\/workflows\/.*\.ya?ml$/i.test(file.filename)) {
      if (/^\s*pull_request_target\s*:/m.test(additions)) reasons.push(`pull-request-target:${file.filename}`);
      if (/^\s*permissions\s*:\s*write-all\s*$/m.test(additions)) reasons.push(`write-all:${file.filename}`);
      if (/(?:curl|wget)[^\n|]*\|\s*(?:ba|z|fi)?sh\b/i.test(additions)) reasons.push(`remote-shell-pipe:${file.filename}`);
      if (/\beval\s+["']?\$/.test(additions)) reasons.push(`eval-shell:${file.filename}`);
    }
  }
  return [...new Set(reasons)];
}

function latestReviewStates(reviews) {
  const latest = new Map();
  for (const review of reviews) {
    if (!review.user?.login || ['COMMENTED', 'PENDING', 'DISMISSED'].includes(review.state)) continue;
    latest.set(review.user.login, review.state);
  }
  return latest;
}

function checkState(runs, status) {
  const checkRuns = runs.check_runs || [];
  const statuses = status.statuses || [];
  if (!checkRuns.length && !statuses.length) return 'none';
  for (const run of checkRuns) {
    if (run.status !== 'completed') return 'pending';
    if (!['success', 'neutral', 'skipped'].includes(run.conclusion)) return 'failed';
  }
  for (const entry of statuses) {
    if (entry.state === 'pending') return 'pending';
    if (['failure', 'error'].includes(entry.state)) return 'failed';
  }
  return checkRuns.some((run) => run.conclusion === 'success') || statuses.some((entry) => entry.state === 'success')
    ? 'green'
    : 'neutral-only';
}

function tier(entry) {
  const [owner] = entry.repo.split('/');
  const text = `${entry.title}\n${entry.body}`.toLowerCase();
  if (owner.endsWith('-test')) return 'A-test-org';
  if (text.includes('semantic conflict resolution') && /validation|tests?|checks?/.test(text)) return 'B-semantic-resolution';
  if (entry.changed_files <= 10 && entry.additions + entry.deletions <= 2500 && /validation|tests?|checks?/.test(text)) return 'C-small-validated';
  return 'D-other';
}

async function inspect(item) {
  const repo = item.repository_url.replace(`${api}/repos/`, '');
  let pr = await request(`${api}/repos/${repo}/pulls/${item.number}`);
  for (let attempt = 0; pr.mergeable == null && attempt < 4; attempt += 1) {
    await sleep(700 + attempt * 500);
    pr = await request(`${api}/repos/${repo}/pulls/${item.number}`);
  }

  const [repository, reviews, files, issueComments, runs, status] = await Promise.all([
    request(`${api}/repos/${repo}`),
    request(`${api}/repos/${repo}/pulls/${item.number}/reviews?per_page=100`),
    request(`${api}/repos/${repo}/pulls/${item.number}/files?per_page=100`),
    item.comments ? request(`${api}/repos/${repo}/issues/${item.number}/comments?per_page=100`) : Promise.resolve([]),
    request(`${api}/repos/${repo}/commits/${pr.head.sha}/check-runs?per_page=100`),
    request(`${api}/repos/${repo}/commits/${pr.head.sha}/status?per_page=100`),
  ]);

  const reasons = [];
  const labels = item.labels || [];
  const body = item.body || '';
  const allCommentText = issueComments.map((comment) => comment.body || '').join('\n');
  const allText = `${item.title || ''}\n${body}\n${allCommentText}`;
  const labelText = labels.map((label) => label.name).join(' ');

  if (repo === 'ORESoftware/k8s-cluster' && [231, 792].includes(item.number)) reasons.push('hard-veto');
  if (vetoPattern.test(allText) || vetoPattern.test(labelText)) reasons.push('text-veto');
  if (concernPattern.test(allCommentText)) reasons.push('comment-concern');
  if (body.toLowerCase().includes('source-gated until:') && !body.toLowerCase().includes('all declared source repositories resolved')) reasons.push('source-gated');
  if (pr.state !== 'open' || pr.draft) reasons.push('not-open-ready');
  if (pr.user?.login !== 'ORESoftware') reasons.push('wrong-author');
  if (repository.archived) reasons.push('archived');
  if (!(repository.permissions?.admin || repository.permissions?.maintain || repository.permissions?.push)) reasons.push('no-write-permission');
  if (pr.base?.ref !== repository.default_branch) reasons.push('non-default-base');
  if (pr.head?.repo?.full_name !== repo) reasons.push('cross-repository-head');
  if (pr.mergeable !== true) reasons.push(`mergeable-${String(pr.mergeable)}`);
  if (pr.mergeable_state !== 'clean') reasons.push(`merge-state-${pr.mergeable_state}`);
  if (pr.review_comments !== 0) reasons.push('inline-review-comments');
  if ([...latestReviewStates(reviews).values()].includes('CHANGES_REQUESTED')) reasons.push('changes-requested');
  if (pr.changed_files < 1 || pr.changed_files > 100 || files.length !== pr.changed_files) reasons.push('file-count');
  if (pr.additions + pr.deletions > 12000) reasons.push('change-too-large');
  const checks = checkState(runs, status);
  if (checks !== 'green') reasons.push(`checks-${checks}`);
  reasons.push(...patchReasons(files));

  const entry = {
    repo,
    number: item.number,
    title: item.title,
    body,
    created_at: item.created_at,
    head_sha: pr.head.sha,
    base_sha: pr.base.sha,
    mergeable_state: pr.mergeable_state,
    changed_files: pr.changed_files,
    additions: pr.additions,
    deletions: pr.deletions,
    issue_comments: item.comments,
    check_state: checks,
    reasons: [...new Set(reasons)],
  };
  entry.tier = tier(entry);
  return entry;
}

const query = encodeURIComponent(`is:pr is:open author:ORESoftware draft:false created:<${cutoff} sort:created-asc`);
const byUrl = new Map();
let total = 0;
for (let page = 1; page <= 10; page += 1) {
  const result = await request(`${api}/search/issues?q=${query}&per_page=100&page=${page}`);
  total = result.total_count;
  for (const item of result.items || []) byUrl.set(item.html_url, item);
  if ((result.items || []).length < 100) break;
}
const inspected = await mapLimit([...byUrl.values()], 10, inspect);
const errors = inspected.filter((entry) => entry.audit_error);
const valid = inspected.filter((entry) => !entry.audit_error);
const eligible = valid.filter((entry) => entry.reasons.length === 0);

const byRepo = new Map();
for (const entry of eligible.sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)))) {
  if (!byRepo.has(entry.repo)) byRepo.set(entry.repo, entry);
}
const unique = [...byRepo.values()];
const tiers = {};
for (const entry of unique) tiers[entry.tier] = (tiers[entry.tier] || 0) + 1;
const skip = new Map();
for (const entry of valid) for (const reason of entry.reasons) skip.set(reason, (skip.get(reason) || 0) + 1);

console.log(`PRODUCTION_AUDIT_SUMMARY ${JSON.stringify({
  search_total: total,
  observed: valid.length,
  audit_errors: errors.length,
  eligible: eligible.length,
  unique_repositories: unique.length,
  tiers,
})}`);
console.log('CANDIDATES');
for (const entry of unique) {
  console.log(`${entry.tier}\t${entry.repo}#${entry.number}\t${entry.head_sha}\t${entry.changed_files} files\t${entry.additions + entry.deletions} lines\tcomments=${entry.issue_comments}\t${entry.title}`);
}
console.log('SKIP_REASONS');
for (const [reason, count] of [...skip.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))) {
  console.log(`${count}\t${reason}`);
}
for (const error of errors.slice(0, 20)) console.log(`AUDIT_ERROR ${JSON.stringify(error)}`);

const lines = [
  '## Comment-aware ready PR audit',
  '',
  `- Search total: ${total}`,
  `- Inspected: ${valid.length}`,
  `- Audit errors: ${errors.length}`,
  `- Eligible exact heads: ${eligible.length}`,
  `- Unique eligible repositories: ${unique.length}`,
  '',
  '### Tiers',
  '',
  ...Object.entries(tiers).map(([name, count]) => `- ${name}: ${count}`),
  '',
  '### Leading skip reasons',
  '',
  ...[...skip.entries()].sort((a, b) => b[1] - a[1]).slice(0, 20).map(([reason, count]) => `- ${reason}: ${count}`),
];
if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, `${lines.join('\n')}\n`);
