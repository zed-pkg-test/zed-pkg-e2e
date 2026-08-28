import fs from 'node:fs';

const token = process.env.GH_TOKEN;
const apply = process.env.APPLY === 'true';
const target = Number(process.env.TARGET || '509');
const searchLimit = 1000;

if (!token) throw new Error('GH_TOKEN is required');
if (!Number.isInteger(target) || target < 1) throw new Error(`invalid TARGET: ${process.env.TARGET}`);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const apiBase = 'https://api.github.com';
const headers = {
  Accept: 'application/vnd.github+json',
  Authorization: `Bearer ${token}`,
  'X-GitHub-Api-Version': '2022-11-28',
  'User-Agent': 'exact-head-semantic-pr-fleet-merger',
};

async function request(url, options = {}, attempts = 6) {
  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      const response = await fetch(url, {
        ...options,
        headers: { ...headers, ...(options.headers || {}) },
      });
      const text = await response.text();
      const value = text ? JSON.parse(text) : null;
      if (response.ok) return value;

      const retryable = response.status === 429 || response.status >= 500 ||
        (response.status === 403 && /secondary rate limit|abuse detection/i.test(text));
      if (!retryable || attempt === attempts) {
        const error = new Error(`${options.method || 'GET'} ${url} -> ${response.status}: ${text.slice(0, 1200)}`);
        error.status = response.status;
        error.response = value;
        throw error;
      }
      const retryAfter = Number(response.headers.get('retry-after') || 0);
      await sleep(Math.max(retryAfter * 1000, attempt * attempt * 1000));
    } catch (error) {
      lastError = error;
      if (attempt === attempts || (error.status && error.status < 500 && error.status !== 429)) throw error;
      await sleep(attempt * attempt * 1000);
    }
  }
  throw lastError;
}

async function graphql(query, variables = {}) {
  const value = await request(`${apiBase}/graphql`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, variables }),
  });
  if (value.errors?.length) {
    throw new Error(`GraphQL errors: ${JSON.stringify(value.errors).slice(0, 4000)}`);
  }
  return value.data;
}

const pullRequestFields = `
  id
  number
  title
  body
  url
  isDraft
  createdAt
  updatedAt
  headRefOid
  baseRefName
  mergeable
  mergeStateStatus
  reviewDecision
  additions
  deletions
  changedFiles
  author { login }
  labels(first: 30) { nodes { name } }
  comments(first: 1) { totalCount }
  headRepository { nameWithOwner }
  repository {
    nameWithOwner
    isArchived
    viewerPermission
    defaultBranchRef { name }
    pullRequests(states: OPEN, first: 1) { totalCount }
    mergeCommitAllowed
    squashMergeAllowed
    rebaseMergeAllowed
  }
  reviewThreads(first: 100) {
    nodes { isResolved }
    pageInfo { hasNextPage }
  }
  reviews(last: 100) {
    nodes { state submittedAt author { login } }
    pageInfo { hasPreviousPage }
  }
  commits(last: 1) {
    nodes {
      commit {
        oid
        statusCheckRollup {
          state
          contexts(first: 100) {
            nodes {
              __typename
              ... on CheckRun { name status conclusion }
              ... on StatusContext { context state }
            }
            pageInfo { hasNextPage }
          }
        }
      }
    }
  }
  files(first: 100) {
    totalCount
    nodes { path additions deletions changeType }
    pageInfo { hasNextPage }
  }
`;

const searchQuery = `
  query OpenPullRequests($query: String!, $cursor: String) {
    search(query: $query, type: ISSUE, first: 20, after: $cursor) {
      issueCount
      pageInfo { hasNextPage endCursor }
      nodes {
        ... on PullRequest { ${pullRequestFields} }
      }
    }
    rateLimit { cost remaining resetAt }
  }
`;

const onePullRequestQuery = `
  query OnePullRequest($owner: String!, $name: String!, $number: Int!) {
    repository(owner: $owner, name: $name) {
      pullRequest(number: $number) { ${pullRequestFields} }
    }
    rateLimit { cost remaining resetAt }
  }
`;

const readyMutation = `
  mutation Ready($id: ID!) {
    markPullRequestReadyForReview(input: { pullRequestId: $id }) {
      pullRequest { id isDraft headRefOid mergeable mergeStateStatus }
    }
  }
`;

const draftMutation = `
  mutation Draft($id: ID!) {
    convertPullRequestToDraft(input: { pullRequestId: $id }) {
      pullRequest { id isDraft headRefOid }
    }
  }
`;

function repoParts(nameWithOwner) {
  const slash = nameWithOwner.indexOf('/');
  return [nameWithOwner.slice(0, slash), nameWithOwner.slice(slash + 1)];
}

function textOf(pr) {
  return `${pr.title || ''}\n${pr.body || ''}`.toLowerCase();
}

function isExplicitVeto(pr) {
  const repo = pr.repository?.nameWithOwner?.toLowerCase();
  const text = textOf(pr);
  const labels = (pr.labels?.nodes || []).map((label) => label.name.toLowerCase());
  return (repo === 'oresoftware/k8s-cluster' && pr.number === 231) ||
    (repo === 'oresoftware/k8s-cluster' && pr.number === 792) ||
    /do not merge|don['’]?t merge|never merge|temporary synchronization|\bwip\b|work in progress/.test(text) ||
    labels.some((label) => /do[- ]?not[- ]?merge|never[- ]?merge|\bwip\b|hold|blocked/.test(label));
}

function isGeneratedOrTestFleet(pr) {
  const repo = pr.repository?.nameWithOwner || '';
  const owner = repo.split('/')[0].toLowerCase();
  const title = (pr.title || '').trim();
  const lowerTitle = title.toLowerCase();
  const body = (pr.body || '').toLowerCase();
  const factoryTitle =
    /^test: bootstrap (?:independent acceptance portfolio|[a-z0-9][a-z0-9-]* harness)$/i.test(title) ||
    lowerTitle === 'chore: bootstrap test organization governance' ||
    lowerTitle === 'test: add isolated playwright, puppeteer, and selenium harness contract';
  const testOrg = owner.endsWith('-test');
  const evidence =
    body.includes('github-test-org-factory') ||
    body.includes('generated') ||
    body.includes('declared readiness: `ready`') ||
    body.includes('declared readiness: ready') ||
    body.includes('harness') ||
    body.includes('validation') ||
    body.includes('verification') ||
    body.includes('tests');
  const testShapedTitle = /^(test|ci|chore)(:|\b)/i.test(title);
  return factoryTitle || (testOrg && testShapedTitle && evidence);
}

function hasReadinessEvidence(pr) {
  const body = (pr.body || '').toLowerCase();
  return /\b(validation|verification|tests?|checks?)\b/.test(body);
}

function latestReviewStates(pr) {
  const latest = new Map();
  for (const review of pr.reviews?.nodes || []) {
    const login = review.author?.login;
    if (!login) continue;
    if (review.state === 'COMMENTED' || review.state === 'PENDING') continue;
    latest.set(login, review.state);
  }
  return latest;
}

function checksAreSuccessful(pr) {
  const commit = pr.commits?.nodes?.at(-1)?.commit;
  if (!commit || commit.oid !== pr.headRefOid) return false;
  const rollup = commit.statusCheckRollup;
  if (!rollup || rollup.state !== 'SUCCESS' || rollup.contexts?.pageInfo?.hasNextPage) return false;
  const contexts = rollup.contexts?.nodes || [];
  if (!contexts.length) return false;
  let success = false;
  for (const context of contexts) {
    if (context.__typename === 'CheckRun') {
      if (context.status !== 'COMPLETED') return false;
      if (!['SUCCESS', 'NEUTRAL', 'SKIPPED'].includes(context.conclusion)) return false;
      if (context.conclusion === 'SUCCESS') success = true;
    } else if (context.__typename === 'StatusContext') {
      if (context.state !== 'SUCCESS') return false;
      success = true;
    } else {
      return false;
    }
  }
  return success;
}

function sensitivePath(pathname) {
  const path = pathname.toLowerCase();
  if (/(^|\/)(id_rsa|id_dsa|id_ecdsa|id_ed25519)(\.|$)/.test(path)) return true;
  if (/\.(pem|p12|pfx|jks|keystore|key)$/.test(path)) return true;
  if (/(^|\/)(credentials?|secrets?|tokens?)(\.|\/|$)/.test(path)) return true;
  if (/(^|\/)\.env($|\.)/.test(path) && !/\.example$|\.sample$|\.template$/.test(path)) return true;
  return false;
}

function coreReasons(pr) {
  const reasons = [];
  const repo = pr.repository;
  const generated = isGeneratedOrTestFleet(pr);
  if (!repo?.nameWithOwner) reasons.push('missing-repository');
  if (pr.author?.login !== 'ORESoftware') reasons.push('wrong-author');
  if (isExplicitVeto(pr)) reasons.push('explicit-veto');
  if (repo?.isArchived) reasons.push('archived');
  if (!['ADMIN', 'MAINTAIN', 'WRITE'].includes(repo?.viewerPermission)) reasons.push('no-merge-permission');
  if (!repo?.defaultBranchRef?.name || pr.baseRefName !== repo.defaultBranchRef.name) reasons.push('non-default-base');
  if (!pr.headRepository?.nameWithOwner || pr.headRepository.nameWithOwner !== repo?.nameWithOwner) reasons.push('cross-repository-head');
  if (pr.changedFiles < 1 || pr.changedFiles > 100 || pr.files?.pageInfo?.hasNextPage || pr.files?.totalCount !== pr.changedFiles) reasons.push('file-count');
  if (pr.additions + pr.deletions > (generated ? 7500 : 3500)) reasons.push('change-too-large');
  if ((pr.files?.nodes || []).some((file) => sensitivePath(file.path))) reasons.push('sensitive-path');
  if (generated && (pr.files?.nodes || []).some((file) => ['DELETED', 'RENAMED'].includes(file.changeType))) reasons.push('generated-delete-or-rename');
  if (pr.comments?.totalCount !== 0) reasons.push('issue-comments-present');
  if (pr.reviewThreads?.pageInfo?.hasNextPage || (pr.reviewThreads?.nodes || []).some((thread) => !thread.isResolved)) reasons.push('unresolved-review-thread');
  if (pr.reviews?.pageInfo?.hasPreviousPage) reasons.push('too-many-reviews');
  if (pr.reviewDecision === 'CHANGES_REQUESTED' || [...latestReviewStates(pr).values()].includes('CHANGES_REQUESTED')) reasons.push('changes-requested');
  if (!checksAreSuccessful(pr)) reasons.push('checks-not-successful');
  if (pr.mergeable !== 'MERGEABLE') reasons.push(`mergeable-${String(pr.mergeable).toLowerCase()}`);
  const allowedState = pr.isDraft ? pr.mergeStateStatus === 'DRAFT' : pr.mergeStateStatus === 'CLEAN';
  if (!allowedState) reasons.push(`merge-state-${String(pr.mergeStateStatus).toLowerCase()}`);
  if (pr.isDraft && !generated) reasons.push('production-draft');
  if (pr.isDraft && generated && !hasReadinessEvidence(pr)) reasons.push('draft-without-readiness-evidence');
  if (!generated && !pr.isDraft && !hasReadinessEvidence(pr)) reasons.push('production-without-validation-evidence');
  return reasons;
}

function fileSet(pr) {
  return new Set((pr.files?.nodes || []).map((file) => file.path));
}

function intersects(left, right) {
  for (const value of left) if (right.has(value)) return true;
  return false;
}

function addIsolation(snapshot) {
  const groups = new Map();
  for (const pr of snapshot) {
    const repo = pr.repository?.nameWithOwner;
    if (!repo) continue;
    if (!groups.has(repo)) groups.set(repo, []);
    groups.get(repo).push(pr);
  }

  const excludedBulkRepos = new Set([
    'ORESoftware/k8s-cluster',
    'zed-pkg-test/zed-pkg-e2e',
  ]);

  for (const [repo, prs] of groups) {
    const allObserved = prs.length === prs[0]?.repository?.pullRequests?.totalCount;
    const sets = new Map(prs.map((pr) => [pr.number, fileSet(pr)]));
    for (const pr of prs) {
      const reasons = [];
      if (excludedBulkRepos.has(repo)) reasons.push('bulk-excluded-repository');
      if (!allObserved) reasons.push('unobserved-open-prs');
      const mine = sets.get(pr.number);
      for (const other of prs) {
        if (other.number === pr.number) continue;
        if (intersects(mine, sets.get(other.number))) {
          reasons.push('overlapping-open-pr');
          break;
        }
      }
      pr._isolationReasons = reasons;
      pr._generated = isGeneratedOrTestFleet(pr);
    }
  }
}

async function loadSnapshot() {
  const byUrl = new Map();
  let cursor = null;
  let issueCount = null;
  while (byUrl.size < searchLimit) {
    const data = await graphql(searchQuery, {
      query: 'is:pr is:open author:ORESoftware sort:created-desc',
      cursor,
    });
    issueCount ??= data.search.issueCount;
    for (const node of data.search.nodes || []) {
      if (node?.url) byUrl.set(node.url, node);
    }
    console.log(`SNAPSHOT_PAGE observed=${byUrl.size} total=${issueCount} rate_remaining=${data.rateLimit.remaining}`);
    if (!data.search.pageInfo.hasNextPage || byUrl.size >= searchLimit) break;
    cursor = data.search.pageInfo.endCursor;
  }
  const snapshot = [...byUrl.values()];
  addIsolation(snapshot);
  return { snapshot, issueCount };
}

function classify(snapshot) {
  const skipCounts = new Map();
  const candidates = [];
  for (const pr of snapshot) {
    const reasons = [...coreReasons(pr), ...(pr._isolationReasons || [])];
    if (reasons.length) {
      for (const reason of new Set(reasons)) skipCounts.set(reason, (skipCounts.get(reason) || 0) + 1);
    } else {
      candidates.push(pr);
    }
  }
  candidates.sort((a, b) => {
    if (a._generated !== b._generated) return a._generated ? -1 : 1;
    if (a.isDraft !== b.isDraft) return a.isDraft ? -1 : 1;
    if (a.changedFiles !== b.changedFiles) return a.changedFiles - b.changedFiles;
    return new Date(a.createdAt) - new Date(b.createdAt);
  });
  return {
    candidates,
    skipCounts: [...skipCounts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])),
  };
}

async function loadOne(pr) {
  const [owner, name] = repoParts(pr.repository.nameWithOwner);
  const data = await graphql(onePullRequestQuery, { owner, name, number: pr.number });
  return data.repository?.pullRequest || null;
}

function addedLines(patch) {
  return String(patch || '')
    .split('\n')
    .filter((line) => line.startsWith('+') && !line.startsWith('+++'))
    .map((line) => line.slice(1))
    .join('\n');
}

async function patchReasons(pr) {
  const repo = pr.repository.nameWithOwner;
  const files = await request(`${apiBase}/repos/${repo}/pulls/${pr.number}/files?per_page=100`);
  const reasons = [];
  if (!Array.isArray(files) || files.length !== pr.changedFiles) reasons.push('rest-file-count-mismatch');
  for (const file of files || []) {
    if (sensitivePath(file.filename)) reasons.push(`sensitive-path:${file.filename}`);
    if (pr._generated && ['removed', 'renamed'].includes(file.status)) reasons.push(`generated-${file.status}:${file.filename}`);
    if (file.patch == null && file.status !== 'removed') reasons.push(`unreviewable-patch:${file.filename}`);
    const patch = String(file.patch || '');
    const additions = addedLines(patch);
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

async function restoreDraft(id) {
  try {
    await graphql(draftMutation, { id });
  } catch (error) {
    console.error(`DRAFT_RESTORE_FAILED id=${id} error=${error.message}`);
  }
}

async function mergeCandidate(snapshotPr) {
  let pr = await loadOne(snapshotPr);
  if (!pr) return { outcome: 'skipped', reason: 'no-longer-open' };
  pr._generated = snapshotPr._generated;
  if (pr.headRefOid !== snapshotPr.headRefOid) return { outcome: 'skipped', reason: 'head-moved' };

  const freshReasons = coreReasons(pr);
  if (freshReasons.length) return { outcome: 'skipped', reason: `fresh:${freshReasons.join(',')}` };

  const patchFailures = await patchReasons(pr);
  if (patchFailures.length) return { outcome: 'skipped', reason: `patch:${patchFailures.join(',')}` };

  let promoted = false;
  if (pr.isDraft) {
    await graphql(readyMutation, { id: pr.id });
    promoted = true;
    let ready = null;
    for (let attempt = 0; attempt < 6; attempt += 1) {
      await sleep(1500 + attempt * 500);
      ready = await loadOne(pr);
      if (ready && !ready.isDraft && ready.mergeable === 'MERGEABLE' && ready.mergeStateStatus === 'CLEAN') break;
    }
    if (!ready || ready.isDraft || ready.mergeable !== 'MERGEABLE' || ready.mergeStateStatus !== 'CLEAN' || !checksAreSuccessful(ready)) {
      await restoreDraft(pr.id);
      return { outcome: 'skipped', reason: 'promotion-did-not-become-clean' };
    }
    ready._generated = pr._generated;
    pr = ready;
  }

  if (pr.headRefOid !== snapshotPr.headRefOid) {
    if (promoted) await restoreDraft(pr.id);
    return { outcome: 'skipped', reason: 'head-moved-after-promotion' };
  }

  const method = pr.repository.squashMergeAllowed ? 'squash' :
    pr.repository.rebaseMergeAllowed ? 'rebase' :
      pr.repository.mergeCommitAllowed ? 'merge' : null;
  if (!method) {
    if (promoted) await restoreDraft(pr.id);
    return { outcome: 'skipped', reason: 'no-allowed-merge-method' };
  }

  const repo = pr.repository.nameWithOwner;
  let result;
  try {
    result = await request(`${apiBase}/repos/${repo}/pulls/${pr.number}/merge`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sha: pr.headRefOid, merge_method: method }),
    }, 3);
  } catch (error) {
    if (promoted) await restoreDraft(pr.id);
    return { outcome: 'skipped', reason: `merge-api:${error.status || 'error'}:${error.message.slice(0, 300)}` };
  }

  if (!result?.merged) {
    if (promoted) await restoreDraft(pr.id);
    return { outcome: 'skipped', reason: `not-merged:${result?.message || 'unknown'}` };
  }

  const verified = await request(`${apiBase}/repos/${repo}/pulls/${pr.number}`);
  if (!verified?.merged || verified?.head?.sha !== pr.headRefOid) {
    throw new Error(`post-merge verification failed for ${repo}#${pr.number}`);
  }
  return { outcome: 'merged', method, mergeSha: result.sha, headSha: pr.headRefOid, repo, number: pr.number, title: pr.title };
}

function appendSummary(lines) {
  if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, `${lines.join('\n')}\n`);
}

const { snapshot, issueCount } = await loadSnapshot();
const { candidates, skipCounts } = classify(snapshot);
const generatedCandidates = candidates.filter((pr) => pr._generated).length;
const productionCandidates = candidates.length - generatedCandidates;

console.log(`PREFLIGHT_SUMMARY ${JSON.stringify({
  mode: apply ? 'apply' : 'preflight',
  api_total_count: issueCount,
  observed: snapshot.length,
  eligible: candidates.length,
  generated_eligible: generatedCandidates,
  production_eligible: productionCandidates,
  target,
})}`);
console.log('SKIP_REASONS');
for (const [reason, count] of skipCounts.slice(0, 50)) console.log(`${count}\t${reason}`);
console.log('ELIGIBLE_SAMPLE');
for (const pr of candidates.slice(0, 100)) {
  console.log(`${pr._generated ? 'generated' : 'production'}\t${pr.isDraft ? 'draft' : 'ready'}\t${pr.repository.nameWithOwner}#${pr.number}\t${pr.headRefOid}\t${pr.title}`);
}

appendSummary([
  `## ${apply ? 'Apply' : 'Preflight'}: exact-head PR fleet merge`,
  '',
  `- GitHub search total: ${issueCount}`,
  `- PRs observed: ${snapshot.length}`,
  `- Eligible after semantic/isolation gates: ${candidates.length}`,
  `- Generated/test-fleet eligible: ${generatedCandidates}`,
  `- Production ready eligible: ${productionCandidates}`,
  `- Merge target: ${target}`,
]);

if (!apply) {
  if (candidates.length < target) console.log(`::warning::preflight found ${candidates.length} eligible PRs, below target ${target}`);
  process.exit(0);
}

const outcomes = new Map();
const merged = [];
for (const candidate of candidates) {
  if (merged.length >= target) break;
  const key = `${candidate.repository.nameWithOwner}#${candidate.number}`;
  try {
    const result = await mergeCandidate(candidate);
    outcomes.set(result.reason || result.outcome, (outcomes.get(result.reason || result.outcome) || 0) + 1);
    if (result.outcome === 'merged') {
      merged.push(result);
      console.log(`MERGED ${result.repo}#${result.number} head=${result.headSha} merge=${result.mergeSha} method=${result.method} title=${JSON.stringify(result.title)}`);
    } else {
      console.log(`SKIPPED ${key} reason=${result.reason}`);
    }
  } catch (error) {
    outcomes.set('exception', (outcomes.get('exception') || 0) + 1);
    console.error(`SKIPPED ${key} reason=exception:${error.stack || error.message}`);
  }
  await sleep(350);
}

const outcomeCounts = [...outcomes.entries()].sort((a, b) => b[1] - a[1]);
console.log(`APPLY_RESULT ${JSON.stringify({ merged: merged.length, target, outcomes: Object.fromEntries(outcomeCounts) })}`);
appendSummary([
  '',
  `### Result`,
  '',
  `- Successfully merged and post-verified: ${merged.length}`,
  `- Target: ${target}`,
  `- Exact-head guard: required for every merge`,
  `- Hard veto preserved: ORESoftware/k8s-cluster#231`,
  '',
  '### Runtime outcomes',
  '',
  ...outcomeCounts.slice(0, 30).map(([reason, count]) => `- ${reason}: ${count}`),
]);

if (merged.length < target) {
  throw new Error(`merged ${merged.length}, below required target ${target}`);
}
