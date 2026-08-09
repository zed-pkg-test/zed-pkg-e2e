const token = process.env.GH_TOKEN;
const repo = process.env.TARGET_REPOSITORY;
const number = Number(process.env.TARGET_PR);
const expectedHead = process.env.TARGET_HEAD;

if (!token || !repo || !Number.isInteger(number) || !expectedHead) {
  throw new Error('GH_TOKEN, TARGET_REPOSITORY, TARGET_PR, and TARGET_HEAD are required');
}

const api = 'https://api.github.com';
const headers = {
  Accept: 'application/vnd.github+json',
  Authorization: `Bearer ${token}`,
  'X-GitHub-Api-Version': '2022-11-28',
  'User-Agent': 'per-installation-exact-head-merger',
};
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function request(url, options = {}, attempts = 5) {
  let last;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const response = await fetch(url, {
      ...options,
      headers: { ...headers, ...(options.headers || {}) },
    });
    const text = await response.text();
    let value;
    try { value = text ? JSON.parse(text) : null; } catch { value = text; }
    if (response.ok) return value;
    last = new Error(`${options.method || 'GET'} ${url} -> ${response.status}: ${String(text).slice(0, 800)}`);
    if (![429, 500, 502, 503, 504].includes(response.status) || attempt === attempts) throw last;
    const retryAfter = Number(response.headers.get('retry-after') || 0);
    await sleep(Math.max(retryAfter * 1000, attempt * attempt * 800));
  }
  throw last;
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

function patchReasons(files) {
  const reasons = [];
  for (const file of files) {
    const patch = String(file.patch || '');
    const additions = patch.split('\n').filter((line) => line.startsWith('+') && !line.startsWith('+++')).map((line) => line.slice(1)).join('\n');
    if (file.patch == null && file.status !== 'removed') reasons.push(`unreviewable:${file.filename}`);
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

async function checksGreen(sha) {
  const [runs, combined] = await Promise.all([
    request(`${api}/repos/${repo}/commits/${sha}/check-runs?per_page=100`),
    request(`${api}/repos/${repo}/commits/${sha}/status?per_page=100`),
  ]);
  const checkRuns = runs.check_runs || [];
  const statuses = combined.statuses || [];
  if (!checkRuns.length && !statuses.length) return false;
  if (checkRuns.some((run) => run.status !== 'completed' || !['success', 'neutral', 'skipped'].includes(run.conclusion))) return false;
  if (statuses.some((status) => status.state !== 'success')) return false;
  return checkRuns.some((run) => run.conclusion === 'success') || statuses.some((status) => status.state === 'success');
}

let pr = await request(`${api}/repos/${repo}/pulls/${number}`);
if (pr.merged) {
  console.log(`ALREADY_MERGED ${repo}#${number}`);
  process.exit(0);
}
if (pr.state !== 'open') throw new Error(`PR is ${pr.state}, not open`);
if (pr.head?.sha !== expectedHead) throw new Error(`head moved: expected ${expectedHead}, found ${pr.head?.sha}`);
for (let attempt = 0; pr.mergeable == null && attempt < 5; attempt += 1) {
  await sleep(700 + attempt * 500);
  pr = await request(`${api}/repos/${repo}/pulls/${number}`);
}

const [repository, comments, reviews, files, green] = await Promise.all([
  request(`${api}/repos/${repo}`),
  pr.comments ? request(`${api}/repos/${repo}/issues/${number}/comments?per_page=100`) : Promise.resolve([]),
  request(`${api}/repos/${repo}/pulls/${number}/reviews?per_page=100`),
  request(`${api}/repos/${repo}/pulls/${number}/files?per_page=100`),
  checksGreen(expectedHead),
]);

const commentText = comments.map((comment) => comment.body || '').join('\n');
const allText = `${pr.title || ''}\n${pr.body || ''}\n${commentText}\n${(pr.labels || []).map((label) => label.name).join(' ')}`;
const reasons = [];
if (repo === 'ORESoftware/k8s-cluster' && [231, 792].includes(number)) reasons.push('hard-veto');
if (vetoPattern.test(allText)) reasons.push('text-veto');
if (concernPattern.test(commentText)) reasons.push('comment-concern');
if (pr.draft) reasons.push('draft');
if (pr.user?.login !== 'ORESoftware') reasons.push('wrong-author');
if (repository.archived) reasons.push('archived');
if (!(repository.permissions?.admin || repository.permissions?.maintain || repository.permissions?.push)) reasons.push('no-write-permission');
if (pr.base?.ref !== repository.default_branch) reasons.push('non-default-base');
if (pr.head?.repo?.full_name !== repo) reasons.push('cross-repository-head');
if (pr.mergeable !== true || !['clean', 'unstable'].includes(pr.mergeable_state)) reasons.push(`merge-${pr.mergeable}-${pr.mergeable_state}`);
if (pr.comments !== 0 || pr.review_comments !== 0 || comments.length !== 0) reasons.push('comments');
if ([...latestReviewStates(reviews).values()].includes('CHANGES_REQUESTED')) reasons.push('changes-requested');
if (pr.changed_files < 1 || pr.changed_files > 100 || files.length !== pr.changed_files) reasons.push('file-count');
if (!green) reasons.push('checks-not-green');
reasons.push(...patchReasons(files));
if (reasons.length) throw new Error(`fresh merge gate failed: ${[...new Set(reasons)].join(',')}`);

const method = repository.allow_squash_merge ? 'squash' : repository.allow_rebase_merge ? 'rebase' : repository.allow_merge_commit ? 'merge' : null;
if (!method) throw new Error('repository has no allowed merge method');

const result = await request(`${api}/repos/${repo}/pulls/${number}/merge`, {
  method: 'PUT',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ sha: expectedHead, merge_method: method }),
}, 3);
if (!result?.merged) throw new Error(`merge declined: ${result?.message || 'unknown'}`);
const verified = await request(`${api}/repos/${repo}/pulls/${number}`);
if (!verified.merged || verified.head?.sha !== expectedHead) throw new Error('post-merge exact-head verification failed');
console.log(`MERGED ${repo}#${number} head=${expectedHead} merge=${result.sha} method=${method}`);
