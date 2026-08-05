const token = process.env.FLEET_GH_TOKEN;
if (!token) throw new Error('FLEET_GH_TOKEN is required');

const candidates = [
  ['discrete-event-systems/des-simulate.rs', 113, 'e21a25508f7c2c7512f4cf09020dec1d6fbe2299'],
  ['canonical-cloud/canonical-cloud-api-server.rs', 46, 'b72c620e9784eaac3b0ca70630e64de3227a186f'],
  ['ORESoftware/ai-agent-coordinator.rs', 56, '689b2575d3d52cf6b204adde1613efe8de5c5cbc'],
  ['opto-sync/syncer.rs', 2, 'e99cf3c8303739fddd2b28c025b5067d776088bb'],
  ['opto-sync/opto-sync-clients', 40, '098a94f040723d19783e8ff6daa3bc265dfa6eec'],
  ['opto-sync/indexeddb-schema.rs', 2, '8563baf28a6d44e4eea0c70ab526f8339a719360'],
  ['opter-io/opter-sync', 3, '7078d21d8411106e4185b20c8c04d22b854b3119'],
  ['canonical-cloud/canonical-cloud-macos-app.swift', 11, 'fb8cf26f9c92f0f439411b58fc069ceff31b8313'],
  ['benefactor-cc/benefactor-dart-client', 18, 'ceb8ea2f692cc6bb277b9485ab4896f01999c60b'],
  ['benefactor-cc/benefactor-api-server.rs', 17, 'ee881e9ada5c4d5516ab2f80c6810e841b4c19e2'],
];

const vetoPattern = /do not merge|don['’]?t merge|never merge|temporary synchronization|\bwip\b|work in progress|not ready|hold (?:this|off)|blocked by|changes requested|must not merge/i;
const concernPattern = /(?:still|currently) fail(?:ing|s)?|unresolved|needs? (?:a |an |the )?(?:fix|change|update|review)|regression remains|unsafe to merge/i;
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
        'User-Agent': 'graphql-frozen-ready-merger',
      },
      body: JSON.stringify({ query, variables }),
    });
    const value = await response.json().catch(() => null);
    if (response.ok && !value?.errors?.length) return value.data;
    last = new Error(`GraphQL failed: ${JSON.stringify(value).slice(0, 1500)}`);
    if (attempt === attempts) throw last;
    await sleep(attempt * attempt * 800);
  }
  throw last;
}

const query = `
  query Candidate($owner: String!, $name: String!, $number: Int!) {
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
        headRefOid
        baseRefName
        mergeable
        mergeStateStatus
        reviewDecision
        comments(first: 100) { totalCount pageInfo { hasNextPage } nodes { body } }
        reviewThreads(first: 100) { pageInfo { hasNextPage } nodes { isResolved } }
        reviews(last: 100) { pageInfo { hasPreviousPage } nodes { state author { login } } }
        labels(first: 50) { nodes { name } }
        files(first: 100) { totalCount pageInfo { hasNextPage } nodes { path changeType } }
        commits(last: 1) {
          nodes {
            commit {
              oid
              statusCheckRollup {
                state
                contexts(first: 100) {
                  pageInfo { hasNextPage }
                  nodes {
                    __typename
                    ... on CheckRun { status conclusion }
                    ... on StatusContext { state }
                  }
                }
              }
            }
          }
        }
      }
    }
    rateLimit { remaining resetAt cost }
  }
`;

const mutation = `
  mutation Merge($id: ID!, $head: GitObjectID!, $method: PullRequestMergeMethod!) {
    mergePullRequest(input: { pullRequestId: $id, expectedHeadOid: $head, mergeMethod: $method }) {
      pullRequest { id number merged mergedAt headRefOid }
    }
  }
`;

function checksGreen(pr) {
  const commit = pr.commits?.nodes?.at(-1)?.commit;
  if (!commit || commit.oid !== pr.headRefOid) return false;
  const rollup = commit.statusCheckRollup;
  if (!rollup || rollup.state !== 'SUCCESS' || rollup.contexts?.pageInfo?.hasNextPage) return false;
  const contexts = rollup.contexts?.nodes || [];
  if (!contexts.length) return false;
  let success = false;
  for (const context of contexts) {
    if (context.__typename === 'CheckRun') {
      if (context.status !== 'COMPLETED' || !['SUCCESS', 'NEUTRAL', 'SKIPPED'].includes(context.conclusion)) return false;
      if (context.conclusion === 'SUCCESS') success = true;
    } else if (context.__typename === 'StatusContext') {
      if (context.state !== 'SUCCESS') return false;
      success = true;
    } else return false;
  }
  return success;
}

function latestReviewStates(pr) {
  const latest = new Map();
  for (const review of pr.reviews?.nodes || []) {
    if (!review.author?.login || ['COMMENTED', 'PENDING', 'DISMISSED'].includes(review.state)) continue;
    latest.set(review.author.login, review.state);
  }
  return latest;
}

const merged = [];
const skipped = [];
for (const [repo, number, expectedHead] of candidates) {
  const [owner, name] = repo.split('/');
  const data = await graphql(query, { owner, name, number });
  if (data.viewer.login !== 'ORESoftware') throw new Error(`unexpected viewer: ${data.viewer.login}`);
  const repository = data.repository;
  const pr = repository?.pullRequest;
  if (!pr) {
    skipped.push([`${repo}#${number}`, 'not-found']);
    continue;
  }
  if (pr.merged) {
    skipped.push([`${repo}#${number}`, 'already-merged']);
    continue;
  }
  const comments = (pr.comments?.nodes || []).map((comment) => comment.body || '').join('\n');
  const labels = (pr.labels?.nodes || []).map((label) => label.name).join(' ');
  const allText = `${pr.title || ''}\n${pr.body || ''}\n${comments}\n${labels}`;
  const reasons = [];
  if (repo === 'ORESoftware/k8s-cluster' && [231, 792].includes(number)) reasons.push('hard-veto');
  if (pr.headRefOid !== expectedHead) reasons.push('head-moved');
  if (pr.state !== 'OPEN' || pr.isDraft) reasons.push('not-open-ready');
  if (repository.isArchived || !['ADMIN', 'MAINTAIN', 'WRITE'].includes(repository.viewerPermission)) reasons.push('permission');
  if (pr.baseRefName !== repository.defaultBranchRef?.name) reasons.push('non-default-base');
  if (pr.mergeable !== 'MERGEABLE' || !['CLEAN', 'UNSTABLE'].includes(pr.mergeStateStatus)) reasons.push(`merge-${pr.mergeable}-${pr.mergeStateStatus}`);
  if (pr.comments?.totalCount !== 0 || pr.comments?.pageInfo?.hasNextPage) reasons.push('comments');
  if (pr.reviewThreads?.pageInfo?.hasNextPage || (pr.reviewThreads?.nodes || []).some((thread) => !thread.isResolved)) reasons.push('unresolved-review-thread');
  if (pr.reviews?.pageInfo?.hasPreviousPage || [...latestReviewStates(pr).values()].includes('CHANGES_REQUESTED')) reasons.push('changes-requested');
  if (pr.files?.pageInfo?.hasNextPage || pr.files?.totalCount < 1 || pr.files?.totalCount > 100) reasons.push('file-count');
  if (vetoPattern.test(allText) || concernPattern.test(comments)) reasons.push('veto-or-concern');
  if (!checksGreen(pr)) reasons.push('checks-not-green');
  if (reasons.length) {
    skipped.push([`${repo}#${number}`, [...new Set(reasons)].join(',')]);
    console.log(`SKIPPED ${repo}#${number} ${[...new Set(reasons)].join(',')}`);
    continue;
  }
  const method = repository.squashMergeAllowed ? 'SQUASH' : repository.rebaseMergeAllowed ? 'REBASE' : repository.mergeCommitAllowed ? 'MERGE' : null;
  if (!method) {
    skipped.push([`${repo}#${number}`, 'no-merge-method']);
    continue;
  }
  try {
    const result = await graphql(mutation, { id: pr.id, head: expectedHead, method });
    const verified = result.mergePullRequest?.pullRequest;
    if (!verified?.merged || verified.headRefOid !== expectedHead) throw new Error('mutation did not verify exact merged head');
    merged.push(`${repo}#${number}`);
    console.log(`MERGED ${repo}#${number} head=${expectedHead} method=${method}`);
  } catch (error) {
    skipped.push([`${repo}#${number}`, `mutation:${error.message}`]);
    console.error(`SKIPPED ${repo}#${number} mutation error: ${error.message}`);
  }
  await sleep(250);
}

console.log(`GRAPHQL_FROZEN_RESULT ${JSON.stringify({ merged: merged.length, skipped: skipped.length })}`);
if (process.env.GITHUB_STEP_SUMMARY) {
  const lines = [
    '## GraphQL-only frozen ready batch',
    '',
    `- Exact heads merged and verified: ${merged.length}`,
    `- Skipped after live revalidation: ${skipped.length}`,
    '',
    ...skipped.map(([key, reason]) => `- ${key}: ${reason}`),
  ];
  require('node:fs').appendFileSync(process.env.GITHUB_STEP_SUMMARY, lines.join('\n') + '\n');
}
