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

/** Built by zed-e2e's stack boot, or any `zed` on PATH. */
export const ZED_BIN =
  process.env.ZED_BIN ?? path.join(SIBLINGS, "zed-cli", "target", "debug", "zed");

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
 * Runs the zed CLI, returning the exit code rather than throwing, because
 * several assertions here are about a command *failing* the right way.
 */
export async function runZed(
  args: string[],
  opts: { cwd?: string; env?: NodeJS.ProcessEnv } = {},
): Promise<ZedResult> {
  try {
    const { stdout, stderr } = await pexecFile(ZED_BIN, args, {
      cwd: opts.cwd,
      env: { ...process.env, ...opts.env },
      maxBuffer: 64 * 1024 * 1024,
    });
    return { stdout, stderr, code: 0 };
  } catch (err) {
    const e = err as { stdout?: string; stderr?: string; code?: number };
    return { stdout: e.stdout ?? "", stderr: e.stderr ?? "", code: e.code ?? 1 };
  }
}

/** Fails fast with an actionable message rather than a wall of 404s. */
export async function requireStack(): Promise<void> {
  for (const [label, url] of [
    ["api", `${API_URL}/v1/health`],
    ["web", `${WEB_URL}/`],
  ] as const) {
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(5_000) });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch (err) {
      throw new Error(
        `zed-pkg ${label} server is not reachable at ${url} (${String(err)}).\n` +
          `Bring the stack up first:  cd ../zed-e2e && npm run stack:up`,
      );
    }
  }
}
