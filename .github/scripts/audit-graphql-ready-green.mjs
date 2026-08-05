import fs from 'node:fs';

const token = process.env.FLEET_GH_TOKEN;
if (!token) throw new Error('FLEET_GH_TOKEN is required');

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
        'User-Agent': 'graphql-ready-green-audit',
      },
      body: JSON.stringify({ query, variables }),
    });
    const value = await response.json().catch(() => null);
    if (response.ok && value?.data) return { data: value.data, errors: value.errors || [] };
    last = new Error(`GraphQL failed: ${JSON.stringify(value).slice(0, 1800)}`);
    if (attempt === attempts) throw last;
    await sleep(attempt * attempt * 800);
  }
  throw last;
}

const query = `
  query ReadyGreen($search: String!, $cursor: String) {
    viewer { login }
    search(query: $search, type: ISSUE, first: 20, after: $cursor) {
      issueCount
      pageInfo { hasNextPage endCursor }
      nodes {
        ... on PullRequest {
          id number title body url createdAt state isDraft headRefName headRefOid baseRefName
          mergeable mergeStateStatus reviewDecision additions deletions changedFiles
          author { login }
          headRepository { nameWithOwner }
          comments(first: 100) { totalCount pageInfo { hasNextPage } nodes { body } }
          reviewThreads(first: 100) { pageInfo { hasNextPage } nodes { isResolved } }
          reviews(last: 100) { pageInfo { hasPreviousPage } nodes { state author { login } } }
          labels(first: 50) { nodes { name } }
          files(first: 100) { totalCount pageInfo { hasNextPage } nodes { path changeType additions deletions } }
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
                      ... on CheckRun { name status conclusion }
                      ... on StatusContext { context state }
                    }
                  }
                }
              }
            }
          }
          repository {
            nameWithOwner isArchived viewerPermission defaultBranchRef { name }
            mergeCommitAllowed squashMergeAllowed rebaseMergeAllowed
            pullRequests(states: OPEN, first: 1) { totalCount }
          }
        }
      }
    }
    rateLimit { remaining resetAt cost }
  }
`;

const search = 'is:pr is:open author:ORESoftware draft:false created:<2026-08-05T12:45:00Z sort:created-asc';
const vetoPattern = /do not merge|don['’]?t merge|never merge|temporary synchronization|\bwip\b|work in progress|not ready|hold (?:this|off)|blocked by|changes requested|must not merge|keep (?:this )?pr draft/i;
const concernPattern = /(?:still|currently) fail(?:ing|s)?|unresolved|needs? (?:a |an |the )?(?:fix|change|update|review)|regression remains|unsafe to merge/i;

function latestReviewStates(pr) {
  const latest = new Map();
  for (const review of pr.reviews?.nodes || []) {
    if (!review.author?.login || ['COMMENTED', 'PENDING', 'DISMISSED'].includes(review.state)) continue;
    latest.set(review.author.login, review.state);
  }
  return latest;
}

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

function pathReasons(pr) {
  const reasons = [];
  for (const file of pr.files?.nodes || []) {
    const pathname = file.path.toLowerCase();
    if (['DELETED', 'RENAMED'].includes(file.changeType)) reasons.push(`destructive:${file.path}`);
    if (pathname.startsWith('.github/workflows/')) reasons.push(`workflow-change:${file.path}`);
    if (/(^|\/)(id_rsa|id_dsa|id_ecdsa|id_ed25519)(\.|$)/.test(pathname)) reasons.push(`private-key-path:${file.path}`);
    if (/\.(pem|p12|pfx|jks|keystore|key)$/.test(pathname)) reasons.push(`key-material-path:${file.path}`);
    if (/(^|\/)(credentials?|secrets?|tokens?)(\.|\/|$)/.test(pathname)) reasons.push(`secret-path:${file.path}`);
    if (/(^|\/)\.env($|\.)/.test(pathname) && !/\.example$|\.sample$|\.template$/.test(pathname)) reasons.push(`environment-secret-path:${file.path}`);
  }
  return reasons;
}

const all = [];
let cursor = null;
let total = null;
let remaining = null;
do {
  const result = await graphql(query, { search, cursor });
  const data = result.data;
  if (data.viewer.login !== 'ORESoftware') throw new Error(`unexpected viewer ${data.viewer.login}`);
  total ??= data.search.issueCount;
  remaining = data.rateLimit.remaining;
  for (const node of data.search.nodes || []) if (node?.repository?.nameWithOwner) all.push(node);
  cursor = data.search.pageInfo.hasNextPage ? data.search.pageInfo.endCursor : null;
  console.log(`PAGE observed=${all.length} total=${total} graphql_remaining=${remaining}`);
} while (cursor);

const results = [];
for (const pr of all) {
  const repo = pr.repository.nameWithOwner;
  const comments = (pr.comments?.nodes || []).map((comment) => comment.body || '').join('\n');
  const labels = (pr.labels?.nodes || []).map((label) => label.name).join(' ');
  const reasons = [];
  if (repo === 'ORESoftware/k8s-cluster' && [231, 792].includes(pr.number)) reasons.push('hard-veto');
  if (pr.author?.login !== 'ORESoftware') reasons.push('wrong-author');
  if (pr.state !== 'OPEN' || pr.isDraft) reasons.push('not-open-ready');
  if (pr.repository.isArchived || !['ADMIN', 'MAINTAIN', 'WRITE'].includes(pr.repository.viewerPermission)) reasons.push('permission');
  if (pr.baseRefName !== pr.repository.defaultBranchRef?.name) reasons.push('non-default-base');
  if (pr.headRepository?.nameWithOwner !== repo) reasons.push('cross-repository-head');
  if (pr.mergeable !== 'MERGEABLE' || pr.mergeStateStatus !== 'CLEAN') reasons.push(`merge-${pr.mergeable}-${pr.mergeStateStatus}`);
  if (pr.repository.pullRequests?.totalCount !== 1) reasons.push(`open-pr-count-${pr.repository.pullRequests?.totalCount}`);
  if (pr.comments?.totalCount !== 0 || pr.comments?.pageInfo?.hasNextPage) reasons.push('comments');
  if (pr.reviewThreads?.pageInfo?.hasNextPage || (pr.reviewThreads?.nodes || []).some((thread) => !thread.isResolved)) reasons.push('review-thread');
  if (pr.reviews?.pageInfo?.hasPreviousPage || [...latestReviewStates(pr).values()].includes('CHANGES_REQUESTED') || pr.reviewDecision === 'CHANGES_REQUESTED') reasons.push('changes-requested');
  if (pr.files?.pageInfo?.hasNextPage || pr.files?.totalCount !== pr.changedFiles || pr.changedFiles < 1 || pr.changedFiles > 100) reasons.push('file-count');
  if (pr.additions + pr.deletions > 12000) reasons.push('change-too-large');
  if (vetoPattern.test(`${pr.title}\n${pr.body || ''}\n${comments}\n${labels}`) || concernPattern.test(comments)) reasons.push('veto-or-concern');
  if (!checksGreen(pr)) reasons.push('checks-not-green');
  reasons.push(...pathReasons(pr));
  results.push({
    repo, number: pr.number, id: pr.id, title: pr.title, url: pr.url,
    head_ref: pr.headRefName, head_sha: pr.headRefOid, base_ref: pr.baseRefName,
    additions: pr.additions, deletions: pr.deletions, changed_files: pr.changedFiles,
    merge_method: pr.repository.squashMergeAllowed ? 'SQUASH' : pr.repository.rebaseMergeAllowed ? 'REBASE' : pr.repository.mergeCommitAllowed ? 'MERGE' : null,
    reasons: [...new Set(reasons)],
  });
}

const eligible = results.filter((entry) => entry.reasons.length === 0 && entry.merge_method);
const skip = new Map();
for (const entry of results) for (const reason of entry.reasons) skip.set(reason, (skip.get(reason) || 0) + 1);
fs.mkdirSync('ready-green-audit', { recursive: true });
fs.writeFileSync('ready-green-audit/candidates.json', JSON.stringify({ schema_version: 1, audited_at: new Date().toISOString(), search_total: total, entries: eligible }, null, 2) + '\n');
fs.writeFileSync('ready-green-audit/all.json', JSON.stringify({ schema_version: 1, entries: results }, null, 2) + '\n');
console.log(`READY_GREEN_AUDIT ${JSON.stringify({ search_total: total, observed: all.length, eligible: eligible.length, graphql_remaining: remaining })}`);
console.log('ELIGIBLE');
for (const entry of eligible) console.log(`${entry.repo}#${entry.number}\t${entry.head_sha}\t${entry.changed_files} files\t${entry.additions + entry.deletions} lines\t${entry.title}`);
console.log('SKIP_REASONS');
for (const [reason, count] of [...skip.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))) console.log(`${count}\t${reason}`);

if (process.env.GITHUB_STEP_SUMMARY) {
  const lines = [
    '## GraphQL isolated green-ready audit', '',
    `- Search total: ${total}`, `- Observed: ${all.length}`, `- Eligible exact heads: ${eligible.length}`, `- GraphQL points remaining: ${remaining}`, '',
    '### Leading skip reasons', '',
    ...[...skip.entries()].sort((a, b) => b[1] - a[1]).slice(0, 25).map(([reason, count]) => `- ${reason}: ${count}`),
  ];
  fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, lines.join('\n') + '\n');
}
