import fs from 'node:fs';

const token = process.env.GH_TOKEN;
if (!token) throw new Error('GH_TOKEN is required');

const cutoff = '2026-08-05T12:45:00Z';
const api = 'https://api.github.com';
const headers = {
  Accept: 'application/vnd.github+json',
  Authorization: `Bearer ${token}`,
  'X-GitHub-Api-Version': '2022-11-28',
  'User-Agent': 'resolved-generated-draft-auditor',
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
    last = new Error(`GET ${url} -> ${response.status}: ${String(text).slice(0, 700)}`);
    const retryable = response.status === 429 || response.status >= 500 ||
      (response.status === 403 && /secondary rate limit|abuse detection/i.test(text));
    if (!retryable || attempt === attempts) throw last;
    const retryAfter = Number(response.headers.get('retry-after') || 0);
    await sleep(Math.max(retryAfter * 1000, attempt * attempt * 700));
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
const resolvedPattern = /all declared source repositories resolved at bootstrap time|all (?:declared )?source repositories (?:are )?resolved|source repositories resolved|declared readiness:\s*`?ready`?/i;
const generatedTitlePattern = /^(?:test: bootstrap (?:independent acceptance portfolio|[a-z0-9][a-z0-9-]* harness)|chore: bootstrap test organization governance|test: add isolated playwright, puppeteer, and selenium harness contract)$/i;

function latestReviewStates(reviews) {
  const latest = new Map();
  for (const review of reviews) {
    if (!review.user?.login || ['COMMENTED', 'PENDING', 'DISMISSED'].includes(review.state)) continue;
    latest.set(review.user.login, review.state);
  }
  return latest;
}

function checksGreen(runs, combinedStatus) {
  const checkRuns = runs.check_runs || [];
  const statuses = combinedStatus.statuses || [];
  if (!checkRuns.length && !statuses.length) return false;
  if (checkRuns.some((run) => run.status !== 'completed' || !['success', 'neutral', 'skipped'].includes(run.conclusion))) return false;
  if (statuses.some((entry) => entry.state !== 'success')) return false;
  return checkRuns.some((run) => run.conclusion === 'success') || statuses.some((entry) => entry.state === 'success');
}

function sensitivePath(pathname) {
  const path = pathname.toLowerCase();
  return /(^|\/)(id_rsa|id_dsa|id_ecdsa|id_ed25519)(\.|$)/.test(path) ||
    /\.(pem|p12|pfx|jks|keystore|key)$/.test(path) ||
    /(^|\/)(credentials?|secrets?|tokens?)(\.|\/|$)/.test(path) ||
    (/(^|\/)\.env($|\.)/.test(path) && !/\.example$|\.sample$|\.template$/.test(path));
}

function addedLines(patch) {
  return String(patch || '')
    .split('\n')
    .filter((line) => line.startsWith('+') && !line.startsWith('+++'))
    .map((line) => line.slice(1))
    .join('\n');
}

function patchReasons(files) {
  const reasons = [];
  for (const file of files) {
    if (['removed', 'renamed'].includes(file.status)) reasons.push(`destructive-file-change:${file.filename}`);
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
      for (const line of additions.split('\n')) {
        const match = line.match(/^\s*(?:-\s*)?uses:\s*([^\s#]+)\s*$/);
        if (!match) continue;
        const use = match[1];
        if (use.startsWith('./')) continue;
        const at = use.lastIndexOf('@');
        const ref = at >= 0 ? use.slice(at + 1) : '';
        if (!/^[0-9a-f]{40}$/i.test(ref)) reasons.push(`unpinned-action:${file.filename}:${use}`);
      }
    }
  }
  return [...new Set(reasons)];
}

async function inspect(item) {
  const repo = item.repository_url.replace(`${api}/repos/`, '');
  const [owner] = repo.split('/');
  const body = item.body || '';
  const initialReasons = [];
  if (!owner.toLowerCase().endsWith('-test')) initialReasons.push('not-test-org');
  if (!generatedTitlePattern.test(item.title || '')) initialReasons.push('not-generated-title');
  if (/source-gated until:/i.test(body)) initialReasons.push('source-gated');
  if (!resolvedPattern.test(body)) initialReasons.push('no-explicit-source-resolution');
  if (!/node scripts\/validate-plan\.mjs/i.test(body)) initialReasons.push('missing-plan-validation-claim');
  if (vetoPattern.test(`${item.title || ''}\n${body}\n${(item.labels || []).map((label) => label.name).join(' ')}`)) initialReasons.push('text-veto');
  if (initialReasons.length) return { repo, number: item.number, title: item.title, reasons: initialReasons, prefiltered: true };

  let pr = await request(`${api}/repos/${repo}/pulls/${item.number}`);
  for (let attempt = 0; pr.mergeable == null && attempt < 4; attempt += 1) {
    await sleep(700 + attempt * 500);
    pr = await request(`${api}/repos/${repo}/pulls/${item.number}`);
  }

  const [repository, comments, reviews, files, runs, status, openPulls] = await Promise.all([
    request(`${api}/repos/${repo}`),
    item.comments ? request(`${api}/repos/${repo}/issues/${item.number}/comments?per_page=100`) : Promise.resolve([]),
    request(`${api}/repos/${repo}/pulls/${item.number}/reviews?per_page=100`),
    request(`${api}/repos/${repo}/pulls/${item.number}/files?per_page=100`),
    request(`${api}/repos/${repo}/commits/${pr.head.sha}/check-runs?per_page=100`),
    request(`${api}/repos/${repo}/commits/${pr.head.sha}/status?per_page=100`),
    request(`${api}/repos/${repo}/pulls?state=open&per_page=100`),
  ]);

  const commentText = comments.map((comment) => comment.body || '').join('\n');
  const reasons = [];
  if (vetoPattern.test(commentText)) reasons.push('comment-veto');
  if (pr.state !== 'open' || !pr.draft) reasons.push('not-open-draft');
  if (pr.user?.login !== 'ORESoftware') reasons.push('wrong-author');
  if (repository.archived) reasons.push('archived');
  if (!(repository.permissions?.admin || repository.permissions?.maintain || repository.permissions?.push)) reasons.push('no-write-permission');
  if (pr.base?.ref !== repository.default_branch) reasons.push('non-default-base');
  if (pr.head?.repo?.full_name !== repo) reasons.push('cross-repository-head');
  if (pr.mergeable !== true) reasons.push(`mergeable-${String(pr.mergeable)}`);
  if (!['draft', 'clean'].includes(pr.mergeable_state)) reasons.push(`merge-state-${pr.mergeable_state}`);
  if (pr.comments !== 0 || comments.length !== 0) reasons.push('issue-comments');
  if (pr.review_comments !== 0) reasons.push('inline-review-comments');
  if ([...latestReviewStates(reviews).values()].includes('CHANGES_REQUESTED')) reasons.push('changes-requested');
  if (openPulls.length !== 1 || openPulls[0]?.number !== item.number) reasons.push('multiple-open-prs-in-repository');
  if (pr.changed_files < 1 || pr.changed_files > 100 || files.length !== pr.changed_files) reasons.push('file-count');
  if (pr.additions + pr.deletions > 12000) reasons.push('change-too-large');
  if (!checksGreen(runs, status)) reasons.push('checks-not-green');
  reasons.push(...patchReasons(files));

  return {
    repo,
    number: item.number,
    title: item.title,
    head_sha: pr.head.sha,
    base_sha: pr.base.sha,
    mergeable_state: pr.mergeable_state,
    changed_files: pr.changed_files,
    additions: pr.additions,
    deletions: pr.deletions,
    checks: (runs.check_runs || []).length,
    reasons: [...new Set(reasons)],
  };
}

const query = encodeURIComponent(`is:pr is:open author:ORESoftware draft:true created:<${cutoff} sort:created-asc`);
const byUrl = new Map();
let total = 0;
for (let page = 1; page <= 10; page += 1) {
  const result = await request(`${api}/search/issues?q=${query}&per_page=100&page=${page}`);
  total = result.total_count;
  for (const item of result.items || []) byUrl.set(item.html_url, item);
  if ((result.items || []).length < 100) break;
}
const items = [...byUrl.values()];
console.log(`DRAFT_SEARCH total=${total} observed=${items.length}`);

const inspected = await mapLimit(items, 12, inspect);
const errors = inspected.filter((entry) => entry.audit_error);
const valid = inspected.filter((entry) => !entry.audit_error);
const candidates = valid.filter((entry) => entry.reasons.length === 0);
const skip = new Map();
for (const entry of valid) for (const reason of entry.reasons) skip.set(reason, (skip.get(reason) || 0) + 1);

console.log(`RESOLVED_DRAFT_AUDIT ${JSON.stringify({ search_total: total, observed: valid.length, audit_errors: errors.length, eligible: candidates.length })}`);
console.log('ELIGIBLE_DRAFTS');
for (const entry of candidates) {
  console.log(`${entry.repo}#${entry.number}\t${entry.head_sha}\t${entry.changed_files} files\t${entry.additions + entry.deletions} lines\t${entry.title}`);
}
console.log('SKIP_REASONS');
for (const [reason, count] of [...skip.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))) {
  console.log(`${count}\t${reason}`);
}
for (const error of errors.slice(0, 30)) console.log(`AUDIT_ERROR ${JSON.stringify(error)}`);

const lines = [
  '## Resolved generated draft audit',
  '',
  `- Draft search total: ${total}`,
  `- Inspected/prefiltered: ${valid.length}`,
  `- Audit errors: ${errors.length}`,
  `- Eligible exact heads: ${candidates.length}`,
  '',
  '### Leading skip reasons',
  '',
  ...[...skip.entries()].sort((a, b) => b[1] - a[1]).slice(0, 25).map(([reason, count]) => `- ${reason}: ${count}`),
];
if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, `${lines.join('\n')}\n`);
