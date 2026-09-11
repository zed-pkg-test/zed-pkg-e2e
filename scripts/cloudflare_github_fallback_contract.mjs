#!/usr/bin/env node

import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { pathToFileURL } from 'node:url';

function parseArgs(argv) {
  const args = new Map();
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key.startsWith('--')) throw new Error(`unexpected argument: ${key}`);
    const value = argv[index + 1];
    if (!value || value.startsWith('--')) throw new Error(`missing value for ${key}`);
    args.set(key.slice(2), value);
    index += 1;
  }
  const infraRoot = args.get('infra-root');
  if (!infraRoot) throw new Error('--infra-root is required');
  return { infraRoot: path.resolve(infraRoot) };
}

function attestCloudflareConfig(infraRoot) {
  const wranglerPath = path.join(infraRoot, 'workers', 'cdn-proxy', 'wrangler.toml');
  const wrangler = fs.readFileSync(wranglerPath, 'utf8');
  assert.match(wrangler, /^name = "zpkg-cdn"$/m);
  assert.match(wrangler, /^main = "src\/index\.js"$/m);
  assert.match(wrangler, /^REGISTRY_URL = "https:\/\/registry\.zpkg\.net"$/m);
  assert.match(
    wrangler,
    /\[\[r2_buckets\]\][\s\S]*?binding = "ARTIFACTS"[\s\S]*?bucket_name = "zed-pkg-artifacts"/,
    'production CDN Worker must bind the owned ARTIFACTS R2 bucket',
  );
  assert.match(
    wrangler,
    /\[\[routes\]\][\s\S]*?pattern = "cdn\.zpkg\.net\/\*"[\s\S]*?zone_name = "zpkg\.net"/,
    'production CDN Worker must intercept the cdn.zpkg.net zone route',
  );
  return {
    worker_name: 'zpkg-cdn',
    route: 'cdn.zpkg.net/*',
    r2_binding: 'ARTIFACTS',
    r2_bucket: 'zed-pkg-artifacts',
  };
}

function fakeR2(objects, reads) {
  return {
    async get(key) {
      reads.push(key);
      const bytes = objects.get(key);
      if (!bytes) return null;
      return {
        body: new Uint8Array(bytes),
        size: bytes.byteLength,
        httpEtag: `"${crypto.createHash('sha256').update(bytes).digest('hex')}"`,
        writeHttpMetadata(headers) {
          headers.set('content-type', key.endsWith('.zip') ? 'application/zip' : 'application/gzip');
        },
      };
    },
  };
}

function githubFixture(payload, upstreamRequests) {
  const releasePath = '/acme/widget/releases/download/v1.2.3/widget-1.2.3.tar.gz';
  const assetUrl =
    'https://release-assets.githubusercontent.com/acme/widget/widget-1.2.3.tar.gz?sig=test';
  return async (input, init = {}) => {
    const url = typeof input === 'string' ? input : input.url;
    const method = init.method || (typeof input === 'string' ? 'GET' : input.method);
    const headers = new Headers(
      init.headers || (typeof input === 'string' ? undefined : input.headers),
    );
    upstreamRequests.push({ url, method, headers: Object.fromEntries(headers.entries()) });
    assert.equal(
      headers.get('authorization'),
      null,
      'Cloudflare must never send auth to public GitHub fallback',
    );
    assert.equal(
      headers.get('cookie'),
      null,
      'Cloudflare must never send cookies to public GitHub fallback',
    );

    const parsed = new URL(url);
    if (parsed.hostname === 'github.com' && parsed.pathname === releasePath) {
      return new Response(null, { status: 302, headers: { location: assetUrl } });
    }
    if (url === assetUrl) {
      return new Response(new Uint8Array(payload), {
        status: 200,
        headers: {
          'content-length': String(payload.byteLength),
          'content-type': 'application/gzip',
          etag: '"github-fixture"',
        },
      });
    }
    throw new Error(`unexpected upstream request: ${method} ${url}`);
  };
}

async function responseBytes(response) {
  return Buffer.from(await response.arrayBuffer());
}

async function run() {
  const { infraRoot } = parseArgs(process.argv.slice(2));
  const cloudflareConfig = attestCloudflareConfig(infraRoot);
  const workerPath = path.join(infraRoot, 'workers', 'cdn-proxy', 'src', 'index.js');
  const workerUrl = `${pathToFileURL(workerPath).href}?contract=${Date.now()}`;
  const { default: worker } = await import(workerUrl);
  assert.equal(typeof worker?.fetch, 'function', 'cdn-proxy worker must export fetch()');

  const payload = Buffer.from('cloudflare-r2-before-github\n', 'utf8');
  const sha256 = crypto.createHash('sha256').update(payload).digest('hex');
  const contentPath = `/artifacts/${sha256}.tar.gz`;
  const githubPath = '/github/acme/widget/v1.2.3/widget-1.2.3.tar.gz';
  const originalFetch = globalThis.fetch;

  try {
    // Phase 1: an immutable content-addressed object in our R2 bucket must win.
    const r2Reads = [];
    const forbiddenUpstream = [];
    globalThis.fetch = async (...args) => {
      forbiddenUpstream.push(args);
      throw new Error('upstream fetch must not run while the R2 object exists');
    };
    const r2Response = await worker.fetch(
      new Request(`https://cdn.zpkg.net${contentPath}`),
      {
        ARTIFACTS: fakeR2(new Map([[contentPath.slice(1), payload]]), r2Reads),
        FALLBACK_TIMEOUT_MS: '1000',
      },
    );
    assert.equal(r2Response.status, 200);
    assert.equal(r2Response.headers.get('x-zed-edge'), 'cdn');
    assert.equal(r2Response.headers.get('x-zed-source'), 'r2');
    assert.deepEqual(await responseBytes(r2Response), payload);
    assert.deepEqual(r2Reads, [contentPath.slice(1)]);
    assert.equal(forbiddenUpstream.length, 0);

    // Phase 2: after an R2 miss, the client moves to the Cloudflare /github path.
    // Cloudflare performs the credential-free, allowlisted GitHub fetch and
    // returns the bytes from cdn.zpkg.net rather than redirecting the client.
    const missReads = [];
    const upstreamRequests = [];
    globalThis.fetch = githubFixture(payload, upstreamRequests);
    const missResponse = await worker.fetch(
      new Request(`https://cdn.zpkg.net${contentPath}`),
      {
        ARTIFACTS: fakeR2(new Map(), missReads),
        FALLBACK_TIMEOUT_MS: '1000',
      },
    );
    assert.equal(missResponse.status, 404);
    assert.deepEqual(missReads, [contentPath.slice(1)]);
    assert.equal(upstreamRequests.length, 0, 'a digest alone must not guess a GitHub identity');

    const githubResponse = await worker.fetch(
      new Request(`https://cdn.zpkg.net${githubPath}`),
      {
        ARTIFACTS: fakeR2(new Map(), missReads),
        FALLBACK_TIMEOUT_MS: '1000',
      },
    );
    assert.equal(githubResponse.status, 200);
    assert.equal(githubResponse.headers.get('x-zed-edge'), 'cdn');
    assert.equal(githubResponse.headers.get('x-zed-source'), 'github-release');
    assert.deepEqual(await responseBytes(githubResponse), payload);
    assert.equal(upstreamRequests.length, 2, 'GitHub release plus one allowlisted redirect');
    assert.equal(new URL(upstreamRequests[0].url).hostname, 'github.com');
    assert.equal(new URL(upstreamRequests[1].url).hostname, 'release-assets.githubusercontent.com');

    // Coordinate aliases remain non-R2 read oracles: /github is a public proxy,
    // while only the immutable digest path can read the private bucket directly.
    assert.deepEqual(missReads, [contentPath.slice(1)]);

    console.log(
      JSON.stringify(
        {
          ok: true,
          order: ['owned-registry', 'cloudflare-r2', 'cloudflare-github-proxy'],
          cloudflare_config: cloudflareConfig,
          content_path: contentPath,
          github_path: githubPath,
          r2_source: r2Response.headers.get('x-zed-source'),
          fallback_source: githubResponse.headers.get('x-zed-source'),
          upstream_hosts: upstreamRequests.map(({ url }) => new URL(url).hostname),
        },
        null,
        2,
      ),
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
}

run().catch((error) => {
  console.error(error?.stack || String(error));
  process.exitCode = 1;
});
