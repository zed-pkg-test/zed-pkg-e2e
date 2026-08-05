import fs from 'node:fs';

const token = process.env.GH_TOKEN;
if (!token) throw new Error('GH_TOKEN is required');

const api = 'https://api.github.com';
const headers = {
  Accept: 'application/vnd.github+json',
  Authorization: `Bearer ${token}`,
  'X-GitHub-Api-Version': '2022-11-28',
  'User-Agent': 'audited-ready-batch-merger',
};
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const candidates = [
  ['usa-acc/usa-acc.github.io', 4, 'be327e240e575cef371e905405db8c67c102da80'],
  ['anticaptrad/act-api-server.rs', 6, '6968bc0d76e3ea0539285a3df27780d916408a24'],
  ['ORESoftware/k8s-libs-and-shared-defs', 11, '5252a645392e7eb28180fef6aeb3f373dbf8bd5e'],
  ['3FA-app/3fa-web-server.rs', 3, 'b898dce8e004be223c66dc21185a1ab1a3b8b745'],
  ['ORESoftware/soccer-sim-game-engine.rs', 3, '964fbd76a2a07d63bf1f2b2b9aafc631d8a14965'],
  ['daedalus-fab/daedalus-web-server.rs', 1, '52e823e7c5bc852c1ba77ad8d580e763f1fa43ce'],
  ['3FA-app/3FA-desktop.rs', 16, '485f311e41f19040e1ffec67ddcb27b40efd479a'],
  ['declarative-migrations/homebrew-tap', 3, '2658ed2e53a65d8f6eda678e5ed71cd72c194746'],
  ['sonus-auris/sonus-auris-e2e', 8, '25c1e79447ee9d4676e3facc9e855d4dc880dc9e'],
  ['scintilla-run/scintilla-run-e2e', 4, 'f363dad5f4027b1a41cf5c7bd145995ea03f56e4'],
  ['athlet-o/athleto-e2e', 2, 'a51fbd8b113cdb9c6159a6ae66aa52517461a93f'],
  ['ORESoftware/next-loggers.ts', 4, '4f4c26f366cb006af9e99e016e1a1658b83f065c'],
  ['ORESoftware/r2g', 88, 'd4d752d31e35220bf4f9ab9b13ac848a9643edc4'],
  ['StreemPilot/sp-infra', 2, 'fdd0ed3587c5ea5bb9cf6fd6c946450c4b5df1eb'],
  ['StreemPilot/streempilot-clients', 3, '08f29fdf3fa632fcec83900b8aaa3d4ad7d0632c'],
  ['StreemPilot/streempilot-e2e', 3, '73407a79ce39eceb82701516e5c1f9b2fa9455e6'],
  ['StreemPilot/streempilot-monorepo', 1, '54e8c4f6e2345eda6880f68e85be100f71b3a2dc'],
  ['StreemPilot/sp-interfaces', 4, 'afc81102626b319bed242c8411d69a647e77ec74'],
  ['voxletra/vxl-api-server.rs', 4, '19389fddd4c5b9427a447aa9c819105590264de0'],
  ['networking-components/ncc-switch', 3, '33997c7017c868466840ebd46fe8ca21b765f49c'],
  ['networking-components/ncc-router', 3, 'd9a65ddc96e5948d26168d41b004381cbd06c862'],
  ['networking-components/ncc-dns-server', 4, '8f7ba85df838d65b42f97b7b87ced092dc331d8b'],
  ['zed-pkg-test/workspace-monorepo', 1, '7f3c0872525245e28fc9463d66cbb2339a3e4ce5'],
  ['discrete-event-systems/des-web.rs', 10, '77741ec8b5331617f71416748ef5f06846e43a5d'],
  ['StreemPilot/streempilot-api-server.rs', 23, 'addbe13705b5550a6659bad2635b4a42e03457cf'],
];

const vetoPattern = /do not merge|don['’]?t merge|never merge|temporary synchronization|\bwip\b|work in progress|not ready|hold (?:this|off)|blocked by|changes requested|must not merge/i;
const concernPattern = /(?:still|currently) fail(?:ing|s)?|unresolved|needs? (?:a |an |the )?(?:fix|change|update|review)|regression remains|unsafe to merge/i;

async function request(url, options = {}, attempts = 6) {
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
    const retryable = response.status === 429 || response.status >= 500 ||
      (response.status === 403 && /secondary rate limit|abuse detection/i.test(text));
    if (!retryable || attempt === attempts) throw last;
    await sleep(attempt * attempt * 800);
  }
  throw last;
}

function latestReviewStates(reviews) {
  const latest = new Map();
  for (const review of reviews) {
    if (!review.user?.login || ['COMMENTED', 'PENDING', 'DISMISSED'].includes(review.state)) continue;
    latest.set(review.user.login, review.state);
  }
  return latest;
}

function checksGreen(runs, status) {
  const checkRuns = runs.check_runs || [];
  const statuses = status.statuses || [];
  if (!checkRuns.length && !statuses.length) return false;
  if (checkRuns.some((run) => run.status !== 'completed' || !['success', 'neutral', 'skipped'].includes(run.conclusion))) return false;
  if (statuses.some((entry) => entry.state !== 'success')) return false;
  return checkRuns.some((run) => run.conclusion === 'success') || statuses.some((entry) => entry.state === 'success');
}

async function revalidate(repo, number, expectedHead) {
  let pr = await request(`${api}/repos/${repo}/pulls/${number}`);
  if (pr.merged) return { state: 'already-merged', pr };
  if (pr.state !== 'open') return { state: 'closed-unmerged', pr };
  if (pr.head?.sha !== expectedHead) return { state: 'head-moved', pr };
  for (let attempt = 0; pr.mergeable == null && attempt < 4; attempt += 1) {
    await sleep(700 + attempt * 500);
    pr = await request(`${api}/repos/${repo}/pulls/${number}`);
  }

  const [repository, comments, reviews, runs, status] = await Promise.all([
    request(`${api}/repos/${repo}`),
    pr.comments ? request(`${api}/repos/${repo}/issues/${number}/comments?per_page=100`) : Promise.resolve([]),
    request(`${api}/repos/${repo}/pulls/${number}/reviews?per_page=100`),
    request(`${api}/repos/${repo}/commits/${expectedHead}/check-runs?per_page=100`),
    request(`${api}/repos/${repo}/commits/${expectedHead}/status?per_page=100`),
  ]);

  const commentText = comments.map((comment) => comment.body || '').join('\n');
  const labelText = (pr.labels || []).map((label) => label.name).join(' ');
  const allText = `${pr.title || ''}\n${pr.body || ''}\n${commentText}\n${labelText}`;
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
  if (pr.mergeable !== true || pr.mergeable_state !== 'clean') reasons.push(`merge-${pr.mergeable}-${pr.mergeable_state}`);
  if (pr.review_comments !== 0) reasons.push('inline-review-comments');
  if ([...latestReviewStates(reviews).values()].includes('CHANGES_REQUESTED')) reasons.push('changes-requested');
  if (!checksGreen(runs, status)) reasons.push('checks-not-green');
  return { state: reasons.length ? 'blocked' : 'eligible', pr, repository, reasons };
}

const merged = [];
const raced = [];
const skipped = [];
for (const [repo, number, expectedHead] of candidates) {
  const key = `${repo}#${number}`;
  try {
    const check = await revalidate(repo, number, expectedHead);
    if (check.state === 'already-merged') {
      raced.push({ key, reason: 'already-merged' });
      console.log(`RACED ${key} already merged`);
      continue;
    }
    if (check.state !== 'eligible') {
      skipped.push({ key, reason: check.state, details: check.reasons || [] });
      console.log(`SKIPPED ${key} ${check.state} ${(check.reasons || []).join(',')}`);
      continue;
    }

    const method = check.repository.squash_merge_allowed ? 'squash' :
      check.repository.rebase_merge_allowed ? 'rebase' :
        check.repository.merge_commit_allowed ? 'merge' : null;
    if (!method) {
      skipped.push({ key, reason: 'no-merge-method' });
      continue;
    }

    const result = await request(`${api}/repos/${repo}/pulls/${number}/merge`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sha: expectedHead, merge_method: method }),
    }, 3);
    if (!result?.merged) {
      skipped.push({ key, reason: result?.message || 'merge-declined' });
      console.log(`SKIPPED ${key} merge declined: ${result?.message || 'unknown'}`);
      continue;
    }
    const verified = await request(`${api}/repos/${repo}/pulls/${number}`);
    if (!verified.merged || verified.head?.sha !== expectedHead) throw new Error(`post-merge verification failed for ${key}`);
    merged.push({ key, head: expectedHead, merge: result.sha, method });
    console.log(`MERGED ${key} head=${expectedHead} merge=${result.sha} method=${method}`);
  } catch (error) {
    skipped.push({ key, reason: error.message });
    console.error(`SKIPPED ${key} exception=${error.message}`);
  }
  await sleep(350);
}

console.log(`BATCH_RESULT ${JSON.stringify({ merged: merged.length, raced: raced.length, skipped: skipped.length })}`);
const lines = [
  '## Audited ready batch result',
  '',
  `- Successfully merged and exact-head verified: ${merged.length}`,
  `- Concurrently merged before this lane: ${raced.length}`,
  `- Skipped after fresh revalidation: ${skipped.length}`,
  '',
  ...skipped.slice(0, 30).map((entry) => `- Skip ${entry.key}: ${entry.reason}${entry.details?.length ? ` (${entry.details.join(', ')})` : ''}`),
];
if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, `${lines.join('\n')}\n`);
