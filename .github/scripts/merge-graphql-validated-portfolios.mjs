import fs from 'node:fs';

const token = process.env.FLEET_GH_TOKEN;
if (!token) throw new Error('FLEET_GH_TOKEN is required');
const document = JSON.parse(fs.readFileSync('portfolio-validated/validated.json', 'utf8'));
if (document.schema_version !== 1) throw new Error('unsupported validation manifest');

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
        'User-Agent': 'graphql-validated-portfolio-merger',
      },
      body: JSON.stringify({ query, variables }),
    });
    const value = await response.json().catch(() => null);
    if (response.ok && !value?.errors?.length) return value.data;
    last = new Error(`GraphQL failed: ${JSON.stringify(value).slice(0, 1800)}`);
    if (attempt === attempts) throw last;
    await sleep(attempt * attempt * 800);
  }
  throw last;
}

const query = `
  query Portfolio($owner: String!, $name: String!, $number: Int!) {
    viewer { login }
    repository(owner: $owner, name: $name) {
      nameWithOwner
      isArchived
      viewerPermission
      defaultBranchRef { name }
      mergeCommitAllowed
      squashMergeAllowed
      rebaseMergeAllowed
      pullRequest(number: $number) {
        id
        number
        title
        body
        state
        merged
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
      }
      pullRequests(states: OPEN, first: 100) {
        pageInfo { hasNextPage }
        nodes { number files(first: 100) { pageInfo { hasNextPage } nodes { path } } }
      }
    }
    rateLimit { remaining resetAt cost }
  }
`;
const readyMutation = `mutation Ready($id: ID!) { markPullRequestReadyForReview(input: { pullRequestId: $id }) { pullRequest { id isDraft headRefOid } } }`;
const draftMutation = `mutation Draft($id: ID!) { convertPullRequestToDraft(input: { pullRequestId: $id }) { pullRequest { id isDraft headRefOid } } }`;
const mergeMutation = `mutation Merge($id: ID!, $head: GitObjectID!, $method: PullRequestMergeMethod!) { mergePullRequest(input: { pullRequestId: $id, expectedHeadOid: $head, mergeMethod: $method }) { pullRequest { id number merged mergedAt headRefOid } } }`;

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

async function gate(entry) {
  const [owner, name] = entry.repo.split('/');
  const data = await graphql(query, { owner, name, number: entry.number });
  if (data.viewer.login !== 'ORESoftware') throw new Error(`unexpected viewer ${data.viewer.login}`);
  const repository = data.repository;
  const pr = repository?.pullRequest;
  if (!pr) return { state: 'not-found' };
  if (pr.merged) return { state: 'already-merged', pr };
  const comments = (pr.comments?.nodes || []).map((comment) => comment.body || '').join('\n');
  const labels = (pr.labels?.nodes || []).map((label) => label.name).join(' ');
  const reasons = [];
  if (pr.headRefOid !== entry.head_sha || pr.headRefName !== entry.head_ref) reasons.push('head-moved');
  if (pr.state !== 'OPEN' || !pr.isDraft) reasons.push('not-open-draft');
  if (repository.isArchived || !['ADMIN', 'MAINTAIN', 'WRITE'].includes(repository.viewerPermission)) reasons.push('permission');
  if (pr.baseRefName !== repository.defaultBranchRef?.name || pr.baseRefName !== entry.base_ref) reasons.push('base-mismatch');
  if (pr.mergeable === 'CONFLICTING' || ['DIRTY', 'BLOCKED', 'UNKNOWN'].includes(pr.mergeStateStatus)) reasons.push(`merge-${pr.mergeable}-${pr.mergeStateStatus}`);
  if (pr.comments?.totalCount !== 0 || pr.comments?.pageInfo?.hasNextPage) reasons.push('comments');
  if (pr.reviewThreads?.pageInfo?.hasNextPage || (pr.reviewThreads?.nodes || []).some((thread) => !thread.isResolved)) reasons.push('review-thread');
  if (pr.reviews?.pageInfo?.hasPreviousPage || [...latestReviewStates(pr).values()].includes('CHANGES_REQUESTED')) reasons.push('changes-requested');
  if (pr.files?.pageInfo?.hasNextPage || pr.files?.totalCount < 1 || pr.files?.totalCount > 100) reasons.push('file-count');
  if ((pr.files?.nodes || []).some((file) => ['DELETED', 'RENAMED'].includes(file.changeType))) reasons.push('destructive-file-change');
  if (vetoPattern.test(`${pr.title}\n${pr.body || ''}\n${comments}\n${labels}`)) reasons.push('veto');
  if (repository.pullRequests?.pageInfo?.hasNextPage) reasons.push('too-many-open-prs');
  if (!reasons.length) {
    const mine = new Set((pr.files?.nodes || []).map((file) => file.path));
    for (const other of repository.pullRequests?.nodes || []) {
      if (other.number === pr.number) continue;
      if (other.files?.pageInfo?.hasNextPage || intersects(mine, new Set((other.files?.nodes || []).map((file) => file.path)))) {
        reasons.push(`overlap-with-pr-${other.number}`);
        break;
      }
    }
  }
  return { state: reasons.length ? 'blocked' : 'eligible', reasons: [...new Set(reasons)], repository, pr, remaining: data.rateLimit.remaining };
}

const merged = [];
const skipped = [];
for (const entry of document.entries || []) {
  const key = `${entry.repo}#${entry.number}`;
  let current;
  try {
    current = await gate(entry);
    if (current.state === 'already-merged') {
      skipped.push([key, 'already-merged']);
      continue;
    }
    if (current.state !== 'eligible') {
      skipped.push([key, `${current.state}:${(current.reasons || []).join(',')}`]);
      console.log(`SKIPPED ${key} ${current.state} ${(current.reasons || []).join(',')}`);
      continue;
    }
    await graphql(readyMutation, { id: current.pr.id });
    let ready = null;
    for (let attempt = 0; attempt < 8; attempt += 1) {
      await sleep(900 + attempt * 350);
      ready = await gate({ ...entry, head_ref: entry.head_ref });
      if (ready.state === 'blocked' && ready.reasons?.length === 1 && ready.reasons[0] === 'not-open-draft') {
        const [owner, name] = entry.repo.split('/');
        const data = await graphql(query, { owner, name, number: entry.number });
        const pr = data.repository?.pullRequest;
        if (pr && !pr.isDraft && pr.headRefOid === entry.head_sha && pr.mergeable !== 'CONFLICTING' && !['DIRTY', 'BLOCKED', 'UNKNOWN'].includes(pr.mergeStateStatus)) {
          ready = { state: 'ready', repository: data.repository, pr };
          break;
        }
      }
    }
    if (ready?.state !== 'ready') {
      await graphql(draftMutation, { id: current.pr.id }).catch(() => {});
      skipped.push([key, 'promotion-did-not-become-mergeable']);
      continue;
    }
    const method = ready.repository.squashMergeAllowed ? 'SQUASH' : ready.repository.rebaseMergeAllowed ? 'REBASE' : ready.repository.mergeCommitAllowed ? 'MERGE' : null;
    if (!method) {
      await graphql(draftMutation, { id: current.pr.id }).catch(() => {});
      skipped.push([key, 'no-merge-method']);
      continue;
    }
    const result = await graphql(mergeMutation, { id: ready.pr.id, head: entry.head_sha, method });
    const verified = result.mergePullRequest?.pullRequest;
    if (!verified?.merged || verified.headRefOid !== entry.head_sha) throw new Error('post-merge exact-head verification failed');
    merged.push(key);
    console.log(`MERGED ${key} head=${entry.head_sha} method=${method}`);
  } catch (error) {
    if (current?.pr?.id) await graphql(draftMutation, { id: current.pr.id }).catch(() => {});
    skipped.push([key, `exception:${error.message}`]);
    console.error(`SKIPPED ${key} exception=${error.stack || error.message}`);
  }
  await sleep(250);
}

console.log(`GRAPHQL_VALIDATED_RESULT ${JSON.stringify({ validated: document.entries?.length || 0, merged: merged.length, skipped: skipped.length })}`);
if (process.env.GITHUB_STEP_SUMMARY) {
  const lines = [
    '## Credential-free validated portfolio merge',
    '',
    `- Exact heads validated without credentials: ${document.entries?.length || 0}`,
    `- Promoted, GraphQL-merged, and exact-head verified: ${merged.length}`,
    `- Skipped after live revalidation: ${skipped.length}`,
    '',
    ...skipped.slice(0, 50).map(([key, reason]) => `- ${key}: ${reason}`),
  ];
  fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, lines.join('\n') + '\n');
}
if (!merged.length) throw new Error('no validated portfolios merged');
