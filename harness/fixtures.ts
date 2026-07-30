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
 *  a TOML parser in for four `key = "value"` lookups is not worth the dep. */
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
  license: string;
}

export function readIdentity(fixture: FixtureRepo): FixtureIdentity {
  const manifest = readFileSync(`${fixturePath(fixture)}/.zpkg.toml`, "utf8");
  return {
    org: manifestField(manifest, "org"),
    name: manifestField(manifest, "name"),
    version: manifestField(manifest, "version"),
    description: manifestField(manifest, "description"),
    license: manifestField(manifest, "license"),
  };
}

/**
 * Publishes one fixture repo. `--skip-vcs-checks` because a CI checkout has no
 * release tag; publishing an already-published version is treated as success so
 * the suite is re-runnable against a stack that was left up (versions are
 * immutable, so a 409 means the registry already holds exactly these bytes).
 */
export async function publishFixture(fixture: FixtureRepo): Promise<FixtureIdentity> {
  const identity = readIdentity(fixture);
  const cwd = fixturePath(fixture);

  const result = await runZed(["publish", "--skip-vcs-checks"], { cwd });
  const alreadyPublished =
    result.code !== 0 && /already (?:exists|published)|409/i.test(result.stderr + result.stdout);

  if (result.code !== 0 && !alreadyPublished) {
    throw new Error(
      `zed publish failed for ${fixture.repo} (exit ${result.code})\n` +
        `${result.stdout}\n${result.stderr}`,
    );
  }

  return identity;
}

let seeded: Promise<Map<string, FixtureIdentity>> | undefined;

/** Publishes every fixture once per process, shared across suites. */
export function ensureSeeded(): Promise<Map<string, FixtureIdentity>> {
  seeded ??= (async () => {
    const published = new Map<string, FixtureIdentity>();
    for (const fixture of [...SINGLE_LANGUAGE_FIXTURES, POLYGLOT_FIXTURE]) {
      published.set(fixture.repo, await publishFixture(fixture));
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
