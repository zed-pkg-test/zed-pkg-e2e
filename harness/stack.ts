/**
 * Connection details for the zed-pkg stack under test.
 *
 * This repo deliberately does NOT boot postgres and the two Rust servers --
 * `zed-pkg/zed-e2e` already owns that orchestration, and a second copy of it
 * would be one more thing to keep in sync for no added coverage. Instead we
 * attach to a stack someone else brought up:
 *
 *   cd ../zed-e2e && npm run stack:up
 *   ZED_E2E_API_URL=http://127.0.0.1:48080 \
 *   ZED_E2E_WEB_URL=http://127.0.0.1:48081 npm run e2e
 *
 * The defaults match zed-e2e's default ports, so the common case needs no env
 * at all.
 */
import { execFile } from "node:child_process";
import { mkdirSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const pexecFile = promisify(execFile);

const HERE = path.dirname(fileURLToPath(import.meta.url));

/** This repo's root, and the directory its sibling checkouts live in. */
export const E2E_ROOT = path.resolve(HERE, "..");
export const SIBLINGS = path.resolve(E2E_ROOT, "..");

export const API_URL = process.env.ZED_E2E_API_URL ?? "http://127.0.0.1:48080";
export const WEB_URL = process.env.ZED_E2E_WEB_URL ?? "http://127.0.0.1:48081";
export const DATABASE_URL =
  process.env.ZED_E2E_DATABASE_URL ??
  "postgres://zed:zed@127.0.0.1:55432/zed_e2e";

/** Built by zed-e2e's stack boot, or explicitly supplied for an external stack. */
export const ZED_BIN =
  process.env.ZED_BIN ?? path.join(SIBLINGS, "zed-cli", "target", "debug", "zed");
export const API_SERVER_BIN =
  process.env.ZED_E2E_API_BIN ??
  path.join(SIBLINGS, "zed-api-server.rs", "target", "debug", "zed-api-server");

/**
 * Every browser worker gets its own disposable zed home. This prevents saved
 * credentials, registry metadata, artifacts, or lock state from the runner (or
 * a developer's real home) from making a supposedly clean test pass.
 */
const ZED_HOME =
  process.env.ZED_E2E_HOME ??
  path.join(os.tmpdir(), "zed-pkg-fixture-e2e", String(process.pid));

/**
 * The fixture repos this suite publishes and then browses. Each must be a
 * sibling checkout; CI clones them, a local run typically already has them.
 *
 * `expectedArtifacts` is the number of packages `zed publish` produces from the
 * repo -- 1 for a single-language fixture, 4 for the polyglot fan-out. It is
 * the whole point of the polyglot case, so it is asserted rather than assumed.
 */
export interface FixtureRepo {
  /** Directory name, and the GitHub repo name under zed-pkg-test. */
  repo: string;
  expectedArtifacts: number;
}

export const SINGLE_LANGUAGE_FIXTURES: FixtureRepo[] = [
  { repo: "node-lib", expectedArtifacts: 1 },
  { repo: "rust-lib", expectedArtifacts: 1 },
  { repo: "go-lib", expectedArtifacts: 1 },
  { repo: "python-lib", expectedArtifacts: 1 },
];

export const POLYGLOT_FIXTURE: FixtureRepo = {
  repo: "polyglot-lib",
  expectedArtifacts: 4,
};

export function fixturePath(fixture: FixtureRepo): string {
  return path.join(SIBLINGS, fixture.repo);
}

export interface ZedResult {
  stdout: string;
  stderr: string;
  code: number;
}

/**
 * Runs the zed CLI against the local test registry and a disposable home,
 * returning the exit code rather than throwing because several assertions are
 * about a command *failing* the right way.
 */
export async function runZed(
  args: string[],
  opts: { cwd?: string; env?: NodeJS.ProcessEnv } = {},
): Promise<ZedResult> {
  mkdirSync(ZED_HOME, { recursive: true });
  try {
    const { stdout, stderr } = await pexecFile(ZED_BIN, args, {
      cwd: opts.cwd,
      env: {
        ...process.env,
        // Never inherit a developer/runner registry, store, or bearer token.
        ZED_PKG_REGISTRY: API_URL,
        ZED_PKG_HOME: ZED_HOME,
        ZED_PKG_TOKEN: undefined,
        ...opts.env,
      },
      maxBuffer: 64 * 1024 * 1024,
    });
    return { stdout, stderr, code: 0 };
  } catch (err) {
    const e = err as { stdout?: string; stderr?: string; code?: number };
    return { stdout: e.stdout ?? "", stderr: e.stderr ?? "", code: e.code ?? 1 };
  }
}

/**
 * Mint an org-scoped publishing token through the same API binary and database
 * used by the running stack. `create-token --org` idempotently creates the
 * namespace, so the fixture suite exercises authenticated publication without
 * enabling the server's insecure auth-disabled bootstrap mode.
 */
export async function createScopedToken(name: string, org: string): Promise<string> {
  try {
    const { stdout } = await pexecFile(
      API_SERVER_BIN,
      ["create-token", "--name", name, "--org", org, "--role", "owner"],
      {
        env: { ...process.env, DATABASE_URL },
        maxBuffer: 16 * 1024 * 1024,
      },
    );
    const lines = stdout.trim().split(/\r?\n/).filter(Boolean);
    const token = lines.at(-1)?.trim();
    if (!token?.startsWith("zpkg_")) {
      throw new Error(`could not parse token from output: ${stdout}`);
    }
    return token;
  } catch (err) {
    const e = err as { stdout?: string; stderr?: string; message?: string };
    throw new Error(
      `could not mint fixture token for ${org}: ${e.stderr ?? e.stdout ?? e.message ?? String(err)}`,
    );
  }
}

interface HealthResponse {
  ok?: unknown;
  db?: unknown;
}

/**
 * Require the server's canonical readiness contract, not merely an open port
 * or an arbitrary non-5xx response. Both zed Rust servers expose `/healthz`
 * and report whether their Postgres connection is usable; browser publishing
 * tests require both the process and its database dependency to be ready.
 */
async function requireHealthy(label: string, baseUrl: string): Promise<void> {
  const url = `${baseUrl}/healthz`;
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(5_000) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const health = (await res.json()) as HealthResponse;
    if (health.ok !== true || health.db !== true) {
      throw new Error(`unhealthy response ${JSON.stringify(health)}`);
    }
  } catch (err) {
    throw new Error(
      `zed-pkg ${label} server is not ready at ${url} (${String(err)}).\n` +
        `Bring the stack up first:  cd ../zed-e2e && npm run stack:up`,
    );
  }
}

/** Fails fast with an actionable message rather than a wall of 404s. */
export async function requireStack(): Promise<void> {
  await requireHealthy("api", API_URL);
  await requireHealthy("web", WEB_URL);
}
