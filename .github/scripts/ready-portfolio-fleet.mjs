import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const mode = process.argv[2];
const api = 'https://api.github.com';
const token = process.env.FLEET_GH_TOKEN || process.env.GH_TOKEN;
if (!['discover', 'merge'].includes(mode)) throw new Error('usage: node ready-portfolio-fleet.mjs <discover|merge>');
if (!token) throw new Error('FLEET_GH_TOKEN is required for this mode');

const headers = {
  Accept: 'application/vnd.github+json',
  Authorization: `Bearer ${token}`,
  'X-GitHub-Api-Version': '2022-11-28',
  'User-Agent': 'ready-portfolio-fleet/1.0',
};
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function request(url, options = {}, attempts = 6) {
  let last;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const response = await fetch(url, {
      ...options,
      headers: { ...headers, ...(options.headers || {}) },
      redirect: options.redirect || 'follow',
    });
    const contentType = response.headers.get('content-type') || '';
    const value = contentType.includes('json')
      ? await response.json().catch(() => null)
      : await response.text();
    if (response.ok) return value;
    last = new Error(`${options.method || 'GET'} ${url} -> ${response.status}: ${JSON.stringify(value).slice(0, 900)}`);
    const retryable = response.status === 429 || response.status >= 500 ||
      (response.status === 403 && /secondary rate limit|abuse detection/i.test(JSON.stringify(value)));
    if (!retryable || attempt === attempts) throw last;
    const retryAfter = Number(response.headers.get('retry-after') || 0);
    await sleep(Math.max(retryAfter * 1000, attempt * attempt * 800));
  }
  throw last;
}

async function requestBytes(url, attempts = 5) {
  let last;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const response = await fetch(url, { headers, redirect: 'follow' });
    if (response.ok) return Buffer.from(await response.arrayBuffer());
    const text = await response.text();
    last = new Error(`GET ${url} -> ${response.status}: ${text.slice(0, 600)}`);
    if (![429, 500, 502, 503, 504].includes(response.status) || attempt === attempts) throw last;
    await sleep(attempt * attempt * 1000);
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
      catch (error) { results[index] = { error: error.message, source: values[index] }; }
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, values.length) }, worker));
  return results;
}

const vetoPattern = /do not merge|don['’]?t merge|never merge|temporary synchronization|\bwip\b|work in progress|not ready|hold (?:this|off)|blocked by|changes requested|must not merge/i;
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
    if (credentialPattern.test(additions)) reasons.push(`credential-pattern:${file.filename}`);
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
        const action = at >= 0 ? use.slice(0, at) : use;
        const ref = at >= 0 ? use.slice(at + 1) : '';
        const immutable = /^[0-9a-f]{40}$/i.test(ref);
        const trustedMajor = /^(actions|github)\//.test(action) && /^v\d+$/.test(ref);
        if (!immutable && !trustedMajor) reasons.push(`untrusted-unpinned-action:${file.filename}:${use}`);
      }
    }
  }
  return [...new Set(reasons)];
}

async function classifyChecks(repo, sha) {
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
    if (run.status !== 'completed') {
      pending += 1;
      continue;
    }
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

async function inspectPortfolio(item) {
  const repo = item.repository_url.replace(`${api}/repos/`, '');
  const [owner] = repo.split('/');
  const reasons = [];
  const body = item.body || '';
  if (!owner.toLowerCase().endsWith('-test')) reasons.push('not-test-org');
  if (item.title !== 'test: bootstrap independent acceptance portfolio') reasons.push('wrong-title');
  if (!/- Declared readiness:\s*`ready`/i.test(body)) reasons.push('not-declared-ready');
  if (/planned_dependency|source-gated until:/i.test(body)) reasons.push('planned-or-source-gated');
  if (vetoPattern.test(`${item.title}\n${body}\n${(item.labels || []).map((label) => label.name).join(' ')}`)) reasons.push('text-veto');
  if (item.comments !== 0) reasons.push('issue-comments');
  if (reasons.length) return { repo, number: item.number, reasons, prefiltered: true };

  let pr = await request(`${api}/repos/${repo}/pulls/${item.number}`);
  for (let attempt = 0; pr.mergeable == null && attempt < 5; attempt += 1) {
    await sleep(700 + attempt * 400);
    pr = await request(`${api}/repos/${repo}/pulls/${item.number}`);
  }

  const [repository, reviews, files, openPulls, checks] = await Promise.all([
    request(`${api}/repos/${repo}`),
    request(`${api}/repos/${repo}/pulls/${item.number}/reviews?per_page=100`),
    request(`${api}/repos/${repo}/pulls/${item.number}/files?per_page=100`),
    request(`${api}/repos/${repo}/pulls?state=open&per_page=100`),
    classifyChecks(repo, pr.head.sha),
  ]);

  if (pr.state !== 'open' || !pr.draft) reasons.push('not-open-draft');
  if (pr.user?.login !== 'ORESoftware') reasons.push('wrong-author');
  if (repository.archived) reasons.push('archived');
  if (!(repository.permissions?.admin || repository.permissions?.maintain || repository.permissions?.push)) reasons.push('no-write-permission');
  if (pr.base?.ref !== repository.default_branch) reasons.push('non-default-base');
  if (pr.head?.repo?.full_name !== repo) reasons.push('cross-repository-head');
  if (pr.mergeable !== true) reasons.push(`mergeable-${String(pr.mergeable)}`);
  if (!['draft', 'clean', 'unstable'].includes(pr.mergeable_state)) reasons.push(`merge-state-${pr.mergeable_state}`);
  if (pr.mergeable_state === 'unstable' && !['billing-only', 'green'].includes(checks.category)) reasons.push('unstable-without-billing-explanation');
  if (pr.comments !== 0 || pr.review_comments !== 0) reasons.push('comments-or-inline-review');
  if ([...latestReviewStates(reviews).values()].includes('CHANGES_REQUESTED')) reasons.push('changes-requested');
  if (openPulls.length !== 1 || openPulls[0]?.number !== item.number) reasons.push('multiple-open-prs-in-repository');
  if (pr.changed_files < 1 || pr.changed_files > 100 || files.length !== pr.changed_files) reasons.push('file-count');
  if (pr.additions + pr.deletions > 20000) reasons.push('change-too-large');
  if (['pending', 'failed'].includes(checks.category)) reasons.push(`checks-${checks.category}`);
  reasons.push(...patchReasons(files));

  return {
    repo,
    number: item.number,
    node_id: pr.node_id,
    title: pr.title,
    head_ref: pr.head.ref,
    head_sha: pr.head.sha,
    base_ref: pr.base.ref,
    base_sha: pr.base.sha,
    private: repository.private,
    changed_files: pr.changed_files,
    additions: pr.additions,
    deletions: pr.deletions,
    checks,
    reasons: [...new Set(reasons)],
  };
}

async function discover() {
  const out = path.resolve('portfolio-bundle');
  const archives = path.join(out, 'archives');
  fs.rmSync(out, { recursive: true, force: true });
  fs.mkdirSync(archives, { recursive: true });

  const query = encodeURIComponent('is:pr is:open author:ORESoftware draft:true created:<2026-08-05T12:45:00Z "Declared readiness: `ready`"');
  const search = await request(`${api}/search/issues?q=${query}&per_page=100&page=1`);
  const inspected = await mapLimit(search.items || [], 10, inspectPortfolio);
  const errors = inspected.filter((entry) => entry.error);
  const valid = inspected.filter((entry) => !entry.error);
  const candidates = valid.filter((entry) => entry.reasons.length === 0);
  const skipCounts = new Map();
  for (const entry of valid) for (const reason of entry.reasons) skipCounts.set(reason, (skipCounts.get(reason) || 0) + 1);

  console.log(`PORTFOLIO_DISCOVERY ${JSON.stringify({ searched: search.total_count, inspected: valid.length, errors: errors.length, candidates: candidates.length })}`);
  for (const [reason, count] of [...skipCounts.entries()].sort((a, b) => b[1] - a[1])) console.log(`SKIP ${count}\t${reason}`);

  const downloaded = await mapLimit(candidates, 6, async (candidate, index) => {
    const bytes = await requestBytes(`${api}/repos/${candidate.repo}/tarball/${candidate.head_sha}`);
    if (bytes.length < 256) throw new Error(`archive too small for ${candidate.repo}#${candidate.number}`);
    const digest = crypto.createHash('sha256').update(bytes).digest('hex');
    const filename = `${String(index).padStart(3, '0')}-${candidate.repo.replace('/', '__')}--pr-${candidate.number}.tar.gz`;
    fs.writeFileSync(path.join(archives, filename), bytes, { mode: 0o600 });
    return { ...candidate, archive: `archives/${filename}`, archive_sha256: digest, archive_bytes: bytes.length };
  });

  const failures = downloaded.filter((entry) => entry.error);
  const manifest = downloaded.filter((entry) => !entry.error);
  fs.writeFileSync(path.join(out, 'manifest.json'), JSON.stringify({ schema_version: 1, generated_at: new Date().toISOString(), entries: manifest, download_failures: failures }, null, 2) + '\n');
  console.log(`PORTFOLIO_BUNDLE ${JSON.stringify({ entries: manifest.length, download_failures: failures.length })}`);
  if (failures.length) for (const failure of failures) console.log(`DOWNLOAD_FAILURE ${JSON.stringify(failure)}`);
  if (!manifest.length) throw new Error('no ready portfolios survived discovery');
}

async function graphql(query, variables) {
  const value = await request(`${api}/graphql`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, variables }),
  });
  if (value.errors?.length) throw new Error(`GraphQL error: ${JSON.stringify(value.errors).slice(0, 1200)}`);
  return value.data;
}

const readyMutation = `mutation Ready($id: ID!) { markPullRequestReadyForReview(input: { pullRequestId: $id }) { pullRequest { id isDraft headRefOid } } }`;
const draftMutation = `mutation Draft($id: ID!) { convertPullRequestToDraft(input: { pullRequestId: $id }) { pullRequest { id isDraft headRefOid } } }`;

async function freshMergeGate(entry) {
  let pr = await request(`${api}/repos/${entry.repo}/pulls/${entry.number}`);
  if (pr.merged) return { state: 'already-merged', pr };
  if (pr.state !== 'open') return { state: 'closed-unmerged', pr };
  if (pr.head?.sha !== entry.head_sha || pr.head?.ref !== entry.head_ref) return { state: 'head-moved', pr };
  for (let attempt = 0; pr.mergeable == null && attempt < 5; attempt += 1) {
    await sleep(700 + attempt * 400);
    pr = await request(`${api}/repos/${entry.repo}/pulls/${entry.number}`);
  }
  const [repository, comments, reviews, openPulls, checks] = await Promise.all([
    request(`${api}/repos/${entry.repo}`),
    pr.comments ? request(`${api}/repos/${entry.repo}/issues/${entry.number}/comments?per_page=100`) : Promise.resolve([]),
    request(`${api}/repos/${entry.repo}/pulls/${entry.number}/reviews?per_page=100`),
    request(`${api}/repos/${entry.repo}/pulls?state=open&per_page=100`),
    classifyChecks(entry.repo, entry.head_sha),
  ]);
  const reasons = [];
  const commentText = comments.map((comment) => comment.body || '').join('\n');
  if (vetoPattern.test(`${pr.title || ''}\n${pr.body || ''}\n${commentText}\n${(pr.labels || []).map((label) => label.name).join(' ')}`)) reasons.push('veto');
  if (!pr.draft) reasons.push('not-draft-before-promotion');
  if (pr.user?.login !== 'ORESoftware') reasons.push('wrong-author');
  if (repository.archived) reasons.push('archived');
  if (!(repository.permissions?.admin || repository.permissions?.maintain || repository.permissions?.push)) reasons.push('no-write-permission');
  if (pr.base?.ref !== repository.default_branch || pr.base?.ref !== entry.base_ref) reasons.push('base-mismatch');
  if (pr.head?.repo?.full_name !== entry.repo) reasons.push('cross-repository-head');
  if (pr.mergeable !== true || ['dirty', 'blocked', 'unknown'].includes(pr.mergeable_state)) reasons.push(`merge-${pr.mergeable}-${pr.mergeable_state}`);
  if (pr.comments !== 0 || pr.review_comments !== 0 || comments.length !== 0) reasons.push('comments');
  if ([...latestReviewStates(reviews).values()].includes('CHANGES_REQUESTED')) reasons.push('changes-requested');
  if (openPulls.length !== 1 || openPulls[0]?.number !== entry.number) reasons.push('multiple-open-prs');
  if (['pending', 'failed'].includes(checks.category)) reasons.push(`checks-${checks.category}`);
  return { state: reasons.length ? 'blocked' : 'eligible', pr, repository, checks, reasons };
}

async function mergeOne(entry) {
  let gate = await freshMergeGate(entry);
  if (gate.state !== 'eligible') return { outcome: gate.state, reasons: gate.reasons || [] };
  await graphql(readyMutation, { id: entry.node_id });
  let promoted = true;
  let ready = null;
  for (let attempt = 0; attempt < 8; attempt += 1) {
    await sleep(1000 + attempt * 400);
    ready = await request(`${api}/repos/${entry.repo}/pulls/${entry.number}`);
    if (!ready.draft && ready.head?.sha === entry.head_sha && ready.mergeable === true && !['dirty', 'blocked', 'unknown'].includes(ready.mergeable_state)) break;
  }
  if (!ready || ready.draft || ready.head?.sha !== entry.head_sha || ready.mergeable !== true || ['dirty', 'blocked', 'unknown'].includes(ready.mergeable_state)) {
    if (promoted) await graphql(draftMutation, { id: entry.node_id }).catch(() => {});
    return { outcome: 'promotion-not-clean' };
  }

  const postChecks = await classifyChecks(entry.repo, entry.head_sha);
  if (['pending', 'failed'].includes(postChecks.category)) {
    await graphql(draftMutation, { id: entry.node_id }).catch(() => {});
    return { outcome: `post-promotion-checks-${postChecks.category}` };
  }

  const method = gate.repository.allow_squash_merge ? 'squash' : gate.repository.allow_rebase_merge ? 'rebase' : gate.repository.allow_merge_commit ? 'merge' : null;
  if (!method) {
    await graphql(draftMutation, { id: entry.node_id }).catch(() => {});
    return { outcome: 'no-merge-method' };
  }

  let result;
  try {
    result = await request(`${api}/repos/${entry.repo}/pulls/${entry.number}/merge`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sha: entry.head_sha, merge_method: method }),
    }, 3);
  } catch (error) {
    await graphql(draftMutation, { id: entry.node_id }).catch(() => {});
    return { outcome: `merge-api-error:${error.message.slice(0, 300)}` };
  }
  if (!result?.merged) {
    await graphql(draftMutation, { id: entry.node_id }).catch(() => {});
    return { outcome: `merge-declined:${result?.message || 'unknown'}` };
  }
  const verified = await request(`${api}/repos/${entry.repo}/pulls/${entry.number}`);
  if (!verified.merged || verified.head?.sha !== entry.head_sha) throw new Error(`post-merge verification failed for ${entry.repo}#${entry.number}`);
  return { outcome: 'merged', method, merge_sha: result.sha };
}

async function mergeValidated() {
  const document = JSON.parse(fs.readFileSync('portfolio-validated/validated.json', 'utf8'));
  if (document.schema_version !== 1) throw new Error('unsupported validated manifest');
  const merged = [];
  const skipped = [];
  for (const entry of document.entries || []) {
    const key = `${entry.repo}#${entry.number}`;
    try {
      const result = await mergeOne(entry);
      if (result.outcome === 'merged') {
        merged.push({ key, head_sha: entry.head_sha, ...result });
        console.log(`MERGED ${key} head=${entry.head_sha} merge=${result.merge_sha} method=${result.method}`);
      } else {
        skipped.push({ key, ...result });
        console.log(`SKIPPED ${key} outcome=${result.outcome} reasons=${(result.reasons || []).join(',')}`);
      }
    } catch (error) {
      skipped.push({ key, outcome: 'exception', error: error.message });
      console.error(`SKIPPED ${key} exception=${error.stack || error.message}`);
    }
    await sleep(350);
  }
  console.log(`PORTFOLIO_MERGE_RESULT ${JSON.stringify({ validated: document.entries?.length || 0, merged: merged.length, skipped: skipped.length })}`);
  if (process.env.GITHUB_STEP_SUMMARY) {
    const lines = [
      '## Ready portfolio merge result',
      '',
      `- Independently validated exact heads: ${document.entries?.length || 0}`,
      `- Promoted, merged, and post-verified: ${merged.length}`,
      `- Skipped after live revalidation: ${skipped.length}`,
      '',
      ...skipped.slice(0, 40).map((entry) => `- ${entry.key}: ${entry.outcome}${entry.reasons?.length ? ` (${entry.reasons.join(', ')})` : ''}`),
    ];
    fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, lines.join('\n') + '\n');
  }
  if (!merged.length) throw new Error('no validated ready portfolios merged');
}

if (mode === 'discover') await discover();
else await mergeValidated();
