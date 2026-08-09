import fs from 'node:fs';

const token = process.env.GH_TOKEN;
if (!token) throw new Error('GH_TOKEN is required');

const cutoff = '2026-08-05T12:45:00Z';
const api = 'https://api.github.com';
const headers = {
  Accept: 'application/vnd.github+json',
  Authorization: `Bearer ${token}`,
  'X-GitHub-Api-Version': '2022-11-28',
  'User-Agent': 'ready-original-pr-auditor',
};
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function request(url, options = {}, attempts = 6) {
  let last;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const response = await fetch(url, {
      ...options,
      headers: { ...headers, ...(options.headers || {}) },
    });
    const text = await response.text();
    let value = null;
    try { value = text ? JSON.parse(text) : null; } catch { value = text; }
    if (response.ok) return value;
    last = new Error(`${options.method || 'GET'} ${url} -> ${response.status}: ${String(text).slice(0, 800)}`);
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
      try { results[index] = await fn(values[index], index); }
      catch (error) { results[index] = { audit_error: error.message, source: values[index] }; }
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, values.length) }, worker));
  return results;
}

function repoFromItem(item) {
  return item.repository_url.replace(`${api}/repos/`, '');
}

function addedLines(patch) {
  return String(patch || '')
    .split('\n')
    .filter((line) => line.startsWith('+') && !line.startsWith('+++'))
    .map((line) => line.slice(1))
    .join('\n');
}

function explicitVeto(repo, number, title, body, labels) {
  if (repo === 'ORESoftware/k8s-cluster' && [231, 792].includes(number)) return true;
  const text = `${title || ''}\n${body || ''}`.toLowerCase();
  const names = labels.map((label) => label.name.toLowerCase());
  return /do not merge|don['’]?t merge|never merge|temporary synchronization|\bwip\b|work in progress/.test(text) ||
    names.some((name) => /do[- ]?not[- ]?merge|never[- ]?merge|\bwip\b|hold|blocked/.test(name));
}

function sourceGated(body) {
  const text = String(body || '').toLowerCase();
  return text.includes('source-gated until:') && !text.includes('all declared source repositories resolved');
}

function sensitivePath(pathname) {
  const path = pathname.toLowerCase();
  return /(^|\/)(id_rsa|id_dsa|id_ecdsa|id_ed25519)(\.|$)/.test(path) ||
    /\.(pem|p12|pfx|jks|keystore|key)$/.test(path) ||
    /(^|\/)(credentials?|secrets?|tokens?)(\.|\/|$)/.test(path) ||
    (/(^|\/)\.env($|\.)/.test(path) && !/\.example$|\.sample$|\.template$/.test(path));
}

function patchSafetyReasons(files) {
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

function latestReviews(reviews) {
  const latest = new Map();
  for (const review of reviews) {
    if (!review.user?.login || ['COMMENTED', 'PENDING', 'DISMISSED'].includes(review.state)) continue;
    latest.set(review.user.login, review.state);
  }
  return latest;
}

async function checkClassification(repo, sha) {
  const [runs, status] = await Promise.all([
    request(`${api}/repos/${repo}/commits/${sha}/check-runs?per_page=100`),
    request(`${api}/repos/${repo}/commits/${sha}/status?per_page=100`),
  ]);
  const checkRuns = runs.check_runs || [];
  const statuses = status.statuses || [];
  const statusFailure = statuses.some((entry) => ['failure', 'error'].includes(entry.state));
  const statusPending = statuses.some((entry) => entry.state === 'pending');
  let billing = 0;
  let realFailures = 0;
  let pending = 0;
  let successes = 0;

  await mapLimit(checkRuns, 6, async (run) => {
    if (run.status !== 'completed') {
      pending += 1;
      return;
    }
    if (['success', 'neutral', 'skipped'].includes(run.conclusion)) {
      if (run.conclusion === 'success') successes += 1;
      return;
    }
    const annotations = await request(`${api}/repos/${repo}/check-runs/${run.id}/annotations?per_page=100`);
    const billingOnly = annotations.length > 0 && annotations.every((annotation) =>
      /job was not started because recent account payments have failed|spending limit needs to be increased/i.test(annotation.message || '')
    );
    if (billingOnly) billing += 1;
    else realFailures += 1;
  });

  if (statusFailure) realFailures += 1;
  if (statusPending) pending += 1;
  let category;
  if (pending) category = 'pending';
  else if (realFailures) category = 'failed';
  else if (billing) category = 'billing-only';
  else if (checkRuns.length === 0 && statuses.length === 0) category = 'no-checks';
  else if (successes > 0 || statuses.some((entry) => entry.state === 'success')) category = 'green';
  else category = 'neutral-only';

  return {
    category,
    check_runs: checkRuns.length,
    statuses: statuses.length,
    billing,
    real_failures: realFailures,
    pending,
    successes,
  };
}

async function inspect(item) {
  const repo = repoFromItem(item);
  const [owner] = repo.split('/');
  let pr = await request(`${api}/repos/${repo}/pulls/${item.number}`);
  for (let attempt = 0; pr.mergeable == null && attempt < 4; attempt += 1) {
    await sleep(800 + attempt * 500);
    pr = await request(`${api}/repos/${repo}/pulls/${item.number}`);
  }
  const [repository, reviews, files, checks] = await Promise.all([
    request(`${api}/repos/${repo}`),
    request(`${api}/repos/${repo}/pulls/${item.number}/reviews?per_page=100`),
    request(`${api}/repos/${repo}/pulls/${item.number}/files?per_page=100`),
    checkClassification(repo, pr.head.sha),
  ]);

  const reasons = [];
  const labels = item.labels || [];
  if (!owner.toLowerCase().endsWith('-test')) reasons.push('not-test-org');
  if (explicitVeto(repo, item.number, item.title, item.body, labels)) reasons.push('explicit-veto');
  if (sourceGated(item.body)) reasons.push('source-gated');
  if (pr.state !== 'open' || pr.draft) reasons.push('not-open-ready');
  if (pr.user?.login !== 'ORESoftware') reasons.push('wrong-author');
  if (repository.archived) reasons.push('archived');
  if (!['admin', 'maintain', 'push'].includes(repository.permissions?.admin ? 'admin' : repository.permissions?.maintain ? 'maintain' : repository.permissions?.push ? 'push' : 'none')) reasons.push('no-write-permission');
  if (pr.base?.ref !== repository.default_branch) reasons.push('non-default-base');
  if (pr.head?.repo?.full_name !== repo) reasons.push('cross-repository-head');
  if (pr.mergeable !== true) reasons.push(`mergeable-${String(pr.mergeable)}`);
  if (['dirty', 'blocked', 'unknown'].includes(pr.mergeable_state)) reasons.push(`merge-state-${pr.mergeable_state}`);
  if (pr.comments !== 0) reasons.push('issue-comments');
  if (pr.review_comments !== 0) reasons.push('review-comments');
  if ([...latestReviews(reviews).values()].includes('CHANGES_REQUESTED')) reasons.push('changes-requested');
  if (pr.changed_files < 1 || pr.changed_files > 100 || files.length !== pr.changed_files) reasons.push('file-count');
  if (pr.additions + pr.deletions > 12000) reasons.push('change-too-large');
  reasons.push(...patchSafetyReasons(files));

  return {
    repo,
    number: item.number,
    title: item.title,
    created_at: item.created_at,
    head_sha: pr.head.sha,
    base_sha: pr.base.sha,
    mergeable: pr.mergeable,
    mergeable_state: pr.mergeable_state,
    changed_files: pr.changed_files,
    additions: pr.additions,
    deletions: pr.deletions,
    check: checks,
    reasons: [...new Set(reasons)],
  };
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
const items = [...byUrl.values()];
console.log(`READY_SEARCH total=${total} observed=${items.length}`);

const inspected = await mapLimit(items, 10, inspect);
const errors = inspected.filter((entry) => entry.audit_error);
const valid = inspected.filter((entry) => !entry.audit_error);
const skipCounts = new Map();
for (const entry of valid) {
  for (const reason of entry.reasons) skipCounts.set(reason, (skipCounts.get(reason) || 0) + 1);
}
const baseEligible = valid.filter((entry) => entry.reasons.length === 0);
const byCheck = Object.fromEntries(
  ['green', 'billing-only', 'no-checks', 'neutral-only', 'pending', 'failed'].map((category) => [
    category,
    baseEligible.filter((entry) => entry.check.category === category).length,
  ])
);

function uniqueByRepo(entries) {
  const map = new Map();
  for (const entry of entries.sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)))) {
    if (!map.has(entry.repo)) map.set(entry.repo, entry);
  }
  return [...map.values()];
}

const green = uniqueByRepo(baseEligible.filter((entry) => entry.check.category === 'green'));
const billing = uniqueByRepo(baseEligible.filter((entry) => entry.check.category === 'billing-only'));
const noChecks = uniqueByRepo(baseEligible.filter((entry) => ['no-checks', 'neutral-only'].includes(entry.check.category)));
const summary = {
  search_total: total,
  observed: items.length,
  inspected: valid.length,
  audit_errors: errors.length,
  base_eligible: baseEligible.length,
  unique_green: green.length,
  unique_billing_only: billing.length,
  unique_no_or_neutral_checks: noChecks.length,
  by_check: byCheck,
};
console.log(`READY_AUDIT_SUMMARY ${JSON.stringify(summary)}`);
console.log('SKIP_REASONS');
for (const [reason, count] of [...skipCounts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))) {
  console.log(`${count}\t${reason}`);
}
for (const [name, entries] of [['GREEN', green], ['BILLING', billing], ['NO_CHECKS', noChecks]]) {
  console.log(`${name}_CANDIDATES`);
  for (const entry of entries.slice(0, 200)) {
    console.log(`${entry.repo}#${entry.number}\t${entry.head_sha}\t${entry.mergeable_state}\t${entry.changed_files} files\t${entry.title}`);
  }
}
for (const error of errors.slice(0, 30)) console.log(`AUDIT_ERROR ${JSON.stringify(error)}`);

const lines = [
  '## Ready original-backlog PR audit',
  '',
  `- Search total: ${total}`,
  `- Inspected: ${valid.length}`,
  `- Audit errors: ${errors.length}`,
  `- Base-eligible test-org PRs: ${baseEligible.length}`,
  `- Unique repositories with green candidates: ${green.length}`,
  `- Unique repositories with billing-only candidates: ${billing.length}`,
  `- Unique repositories with no/neutral-only checks: ${noChecks.length}`,
  '',
  '### Check categories among base-eligible PRs',
  '',
  ...Object.entries(byCheck).map(([category, count]) => `- ${category}: ${count}`),
  '',
  '### Leading skip reasons',
  '',
  ...[...skipCounts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 20).map(([reason, count]) => `- ${reason}: ${count}`),
];
if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, `${lines.join('\n')}\n`);
