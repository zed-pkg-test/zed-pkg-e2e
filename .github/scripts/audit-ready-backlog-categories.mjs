import fs from 'node:fs';

const token = process.env.FLEET_GH_TOKEN || process.env.GH_TOKEN;
if (!token) throw new Error('FLEET_GH_TOKEN is required');
const api = 'https://api.github.com';
const headers = {
  Accept: 'application/vnd.github+json',
  Authorization: `Bearer ${token}`,
  'X-GitHub-Api-Version': '2022-11-28',
  'User-Agent': 'ready-backlog-category-audit/1.0',
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
const concernPattern = /(?:still|currently) fail(?:ing|s)?|unresolved|needs? (?:a |an |the )?(?:fix|change|update|review)|regression remains|unsafe to merge/i;
const credentialPattern = /(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|xox[baprs]-[A-Za-z0-9-]{10,})/;

function latestReviewStates(reviews) {
  const latest = new Map();
  for (const review of reviews) {
    if (!review.user?.login || ['COMMENTED', 'PENDING', 'DISMISSED'].includes(review.state)) continue;
    latest.set(review.user.login, review.state);
  }
  return latest;
}

function sensitivePath(filename) {
  const value = filename.toLowerCase();
  return /(^|\/)(id_rsa|id_dsa|id_ecdsa|id_ed25519)(\.|$)/.test(value) ||
    /\.(pem|p12|pfx|jks|keystore|key)$/.test(value) ||
    /(^|\/)(credentials?|secrets?|tokens?)(\.|\/|$)/.test(value) ||
    (/(^|\/)\.env($|\.)/.test(value) && !/\.example$|\.sample$|\.template$/.test(value));
}

function patchSafety(files) {
  const reasons = [];
  for (const file of files) {
    if (sensitivePath(file.filename)) reasons.push(`sensitive-path:${file.filename}`);
    const patch = String(file.patch || '');
    const additions = patch.split('\n').filter((line) => line.startsWith('+') && !line.startsWith('+++')).map((line) => line.slice(1)).join('\n');
    if (file.patch == null && file.status !== 'removed') reasons.push(`unreviewable-patch:${file.filename}`);
    if (/^(<<<<<<<|=======|>>>>>>>)( |$)/m.test(patch)) reasons.push(`conflict-marker:${file.filename}`);
    if (credentialPattern.test(additions)) reasons.push(`credential-pattern:${file.filename}`);
    if (/\.github\/workflows\/.*\.ya?ml$/i.test(file.filename)) {
      if (/^\s*pull_request_target\s*:/m.test(additions)) reasons.push(`pull-request-target:${file.filename}`);
      if (/^\s*permissions\s*:\s*write-all\s*$/m.test(additions)) reasons.push(`write-all:${file.filename}`);
      if (/(?:curl|wget)[^\n|]*\|\s*(?:ba|z|fi)?sh\b/i.test(additions)) reasons.push(`remote-shell-pipe:${file.filename}`);
      if (/\beval\s+["']?\$/.test(additions)) reasons.push(`eval-shell:${file.filename}`);
    }
  }
  return [...new Set(reasons)];
}

async function checkCategory(repo, sha) {
  const [runs, combined] = await Promise.all([
    request(`${api}/repos/${repo}/commits/${sha}/check-runs?per_page=100`),
    request(`${api}/repos/${repo}/commits/${sha}/status?per_page=100`),
  ]);
  const checkRuns = runs.check_runs || [];
  const statuses = combined.statuses || [];
  let pending = 0;
  let realFailures = 0;
  let billingFailures = 0;
  let successes = 0;
  for (const run of checkRuns) {
    if (run.status !== 'completed') { pending += 1; continue; }
    if (['success', 'neutral', 'skipped'].includes(run.conclusion)) {
      if (run.conclusion === 'success') successes += 1;
      continue;
    }
    const annotations = await request(`${api}/repos/${repo}/check-runs/${run.id}/annotations?per_page=100`);
    const billingOnly = annotations.length > 0 && annotations.every((annotation) =>
      /job was not started because recent account payments have failed|spending limit needs to be increased/i.test(annotation.message || '')
    );
    if (billingOnly) billingFailures += 1;
    else realFailures += 1;
  }
  for (const status of statuses) {
    if (status.state === 'pending') pending += 1;
    else if (['failure', 'error'].includes(status.state)) realFailures += 1;
    else if (status.state === 'success') successes += 1;
  }
  const category = pending ? 'pending' : realFailures ? 'failed' : billingFailures ? 'billing-only' : successes ? 'green' : 'no-checks';
  return { category, pending, realFailures, billingFailures, successes, total: checkRuns.length + statuses.length };
}

async function inspect(item) {
  const repo = item.repository_url.replace(`${api}/repos/`, '');
  let pr = await request(`${api}/repos/${repo}/pulls/${item.number}`);
  for (let attempt = 0; pr.mergeable == null && attempt < 5; attempt += 1) {
    await sleep(700 + attempt * 400);
    pr = await request(`${api}/repos/${repo}/pulls/${item.number}`);
  }
  const [repository, comments, reviews, files, checks, openPulls] = await Promise.all([
    request(`${api}/repos/${repo}`),
    item.comments ? request(`${api}/repos/${repo}/issues/${item.number}/comments?per_page=100`) : Promise.resolve([]),
    request(`${api}/repos/${repo}/pulls/${item.number}/reviews?per_page=100`),
    request(`${api}/repos/${repo}/pulls/${item.number}/files?per_page=100`),
    checkCategory(repo, pr.head.sha),
    request(`${api}/repos/${repo}/pulls?state=open&per_page=100`),
  ]);
  const commentText = comments.map((comment) => comment.body || '').join('\n');
  const allText = `${item.title || ''}\n${item.body || ''}\n${commentText}\n${(item.labels || []).map((label) => label.name).join(' ')}`;
  const gates = [];
  if (repo === 'ORESoftware/k8s-cluster' && [231, 792].includes(item.number)) gates.push('hard-veto');
  if (vetoPattern.test(allText)) gates.push('text-veto');
  if (concernPattern.test(commentText)) gates.push('comment-concern');
  if (pr.state !== 'open' || pr.draft) gates.push('not-open-ready');
  if (pr.user?.login !== 'ORESoftware') gates.push('wrong-author');
  if (repository.archived) gates.push('archived');
  if (!(repository.permissions?.admin || repository.permissions?.maintain || repository.permissions?.push)) gates.push('no-write-permission');
  if (pr.head?.repo?.full_name !== repo) gates.push('cross-repository-head');
  if (pr.comments !== 0 || pr.review_comments !== 0) gates.push('comments');
  if ([...latestReviewStates(reviews).values()].includes('CHANGES_REQUESTED')) gates.push('changes-requested');
  if (pr.changed_files < 1 || pr.changed_files > 100 || files.length !== pr.changed_files) gates.push('file-count');
  if (pr.additions + pr.deletions > 30000) gates.push('change-too-large');
  gates.push(...patchSafety(files));

  const defaultBase = pr.base?.ref === repository.default_branch;
  const multipleOpen = openPulls.length > 1;
  let bucket = 'other';
  if (gates.length) bucket = 'gated';
  else if (!defaultBase) bucket = 'non-default-base';
  else if (pr.mergeable === false || pr.mergeable_state === 'dirty') bucket = 'conflict';
  else if (pr.mergeable == null || ['unknown', 'blocked'].includes(pr.mergeable_state)) bucket = 'blocked-or-unknown';
  else if (multipleOpen) bucket = 'multiple-open-prs';
  else if (checks.category === 'green' && pr.mergeable === true && ['clean', 'unstable'].includes(pr.mergeable_state)) bucket = 'clean-green';
  else if (checks.category === 'billing-only' && pr.mergeable === true) bucket = 'clean-billing-only';
  else if (checks.category === 'no-checks' && pr.mergeable === true) bucket = 'clean-no-checks';
  else if (checks.category === 'pending') bucket = 'checks-pending';
  else if (checks.category === 'failed') bucket = 'checks-failed';

  return {
    repo,
    number: item.number,
    node_id: pr.node_id,
    title: item.title,
    body: item.body || '',
    head_ref: pr.head?.ref,
    head_sha: pr.head?.sha,
    base_ref: pr.base?.ref,
    base_sha: pr.base?.sha,
    default_branch: repository.default_branch,
    mergeable: pr.mergeable,
    mergeable_state: pr.mergeable_state,
    changed_files: pr.changed_files,
    additions: pr.additions,
    deletions: pr.deletions,
    issue_comments: pr.comments,
    review_comments: pr.review_comments,
    open_prs_in_repo: openPulls.length,
    checks,
    gates: [...new Set(gates)],
    bucket,
  };
}

const query = encodeURIComponent('is:pr is:open author:ORESoftware draft:false created:<2026-08-05T12:45:00Z sort:created-asc');
const byUrl = new Map();
let total = 0;
for (let page = 1; page <= 10; page += 1) {
  const result = await request(`${api}/search/issues?q=${query}&per_page=100&page=${page}`);
  total = result.total_count;
  for (const item of result.items || []) byUrl.set(item.html_url, item);
  if ((result.items || []).length < 100) break;
}
const results = await mapLimit([...byUrl.values()], 10, inspect);
const errors = results.filter((entry) => entry.audit_error);
const entries = results.filter((entry) => !entry.audit_error);
const counts = {};
for (const entry of entries) counts[entry.bucket] = (counts[entry.bucket] || 0) + 1;
fs.mkdirSync('ready-backlog-audit', { recursive: true });
fs.writeFileSync('ready-backlog-audit/categories.json', JSON.stringify({ schema_version: 1, audited_at: new Date().toISOString(), search_total: total, entries, errors }, null, 2) + '\n');

console.log(`READY_BACKLOG_CATEGORIES ${JSON.stringify({ search_total: total, inspected: entries.length, errors: errors.length, buckets: counts })}`);
for (const bucket of Object.keys(counts).sort()) {
  console.log(`BUCKET ${bucket} count=${counts[bucket]}`);
  for (const entry of entries.filter((value) => value.bucket === bucket)) {
    console.log(`${entry.repo}#${entry.number}\t${entry.head_sha}\tbase=${entry.base_ref}\tstate=${entry.mergeable_state}\tchecks=${entry.checks.category}\topen=${entry.open_prs_in_repo}\tfiles=${entry.changed_files}\t${entry.title}`);
  }
}
for (const error of errors.slice(0, 30)) console.log(`AUDIT_ERROR ${JSON.stringify(error)}`);

if (process.env.GITHUB_STEP_SUMMARY) {
  const lines = [
    '## Remaining ready backlog categories',
    '',
    `- Search total: ${total}`,
    `- Inspected: ${entries.length}`,
    `- Audit errors: ${errors.length}`,
    '',
    ...Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([bucket, count]) => `- ${bucket}: ${count}`),
  ];
  fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, lines.join('\n') + '\n');
}
