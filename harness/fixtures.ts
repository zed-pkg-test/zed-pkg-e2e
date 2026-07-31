/**
 * Publishes the zed-pkg-test fixture repos into the registry under test.
 *
 * The distinction from zed-e2e's own seeding: that harness writes synthetic
 * manifests to temp dirs, which is right for exercising registry semantics.
 * Here the *inputs are the real fixture repos* -- the same trees the per-repo
 * CI publishes -- so what the browser sees is what a consumer would see.
 */
import { readFileSync } from "node:fs";
import {
  POLYGLOT_FIXTURE,
  SINGLE_LANGUAGE_FIXTURES,
  createScopedToken,
  fixturePath,
  runZed,
  type FixtureRepo,
} from "./stack.js";

export interface PublishedPackage {
  org: string;
  name: string;
  version: string;
  /** Present only for polyglot slices. */
  target?: string;
}

/** Minimal TOML reach-in: these manifests are fixtures we control, and pulling
 *  a TOML parser in for a few `key = "value"` lookups is not worth the dep. */
function manifestField(manifest: string, key: string): string {
  const match = manifest.match(new RegExp(`^${key}\\s*=\\s*"([^"]+)"`, "m"));
  if (!match?.[1]) {
    throw new Error(`.zpkg.toml declares no ${key}`);
  }
  return match[1];
}

export interface FixtureIdentity {
  org: string;
  name: string;
  version: string;
  description: string;
  /** Repository URL is persisted by the registry and rendered by the web UI. */
  repositoryUrl: string;
}

export function readIdentity(fixture: FixtureRepo): FixtureIdentity {
  const manifest = readFileSync(`${fixturePath(fixture)}/.zpkg.toml`, "utf8");
  return {
    org: manifestField(manifest, "org"),
    name: manifestField(manifest, "name"),
    version: manifestField(manifest, "version"),
    description: manifestField(manifest, "description"),
    repositoryUrl: manifestField(manifest, "url"),
  };
}

/**
 * Publishes one fixture repo with an explicitly scoped test token.
 * `--skip-vcs-checks` is necessary because a CI checkout has no release tag;
 * publishing an already-published version is treated as success so the suite
 * stays idempotent when multiple browser files seed the same clean stack.
 */
export async function publishFixture(
  fixture: FixtureRepo,
  token: string,
): Promise<FixtureIdentity> {
  const identity = readIdentity(fixture);
  const cwd = fixturePath(fixture);

  const result = await runZed(["publish", "--skip-vcs-checks"], {
    cwd,
    env: { ZED_PKG_TOKEN: token },
  });
  const alreadyPublished =
    result.code !== 0 && /already (?:exists|published)|version_exists|409/i.test(
      result.stderr + result.stdout,
    );

  if (result.code !== 0 && !alreadyPublished) {
    throw new Error(
      `zed publish failed for ${fixture.repo} (exit ${result.code})\n` +
        `${result.stdout}\n${result.stderr}`,
    );
  }

  return identity;
}

let seeded: Promise<Map<string, FixtureIdentity>> | undefined;

/**
 * Publishes every fixture once per process. Tokens are created per manifest org
 * rather than hard-coding `zed-pkg-test`, so adding a fixture under another
 * namespace remains least-privilege and does not silently broaden authority.
 */
export function ensureSeeded(): Promise<Map<string, FixtureIdentity>> {
  seeded ??= (async () => {
    const published = new Map<string, FixtureIdentity>();
    const tokens = new Map<string, string>();

    for (const fixture of [...SINGLE_LANGUAGE_FIXTURES, POLYGLOT_FIXTURE]) {
      const identity = readIdentity(fixture);
      let token = tokens.get(identity.org);
      if (!token) {
        const safeOrg = identity.org.replace(/[^a-zA-Z0-9_-]/g, "-");
        token = await createScopedToken(
          `fixture-e2e-${safeOrg}-${process.pid}-${Date.now().toString(36)}`,
          identity.org,
        );
        tokens.set(identity.org, token);
      }
      published.set(fixture.repo, await publishFixture(fixture, token));
    }
    return published;
  })();
  return seeded;
}

/** The four packages one polyglot repo fans out to. */
export function polyglotSlices(): Array<{
  suffix: string;
  language: string;
  ecosystem: string;
}> {
  return [
    { suffix: "nodejs", language: "nodejs", ecosystem: "npm" },
    { suffix: "python", language: "python", ecosystem: "pypi" },
    { suffix: "golang", language: "golang", ecosystem: "gomod" },
    { suffix: "rust", language: "rust", ecosystem: "cargo" },
  ];
}
