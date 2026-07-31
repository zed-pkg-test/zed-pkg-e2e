import { expect, test } from "@playwright/test";
import { API_URL, POLYGLOT_FIXTURE, WEB_URL } from "../harness/stack.js";
import { ensureSeeded, polyglotSlices, readIdentity } from "../harness/fixtures.js";

// The polyglot publication claim is that ONE repo becomes FOUR independently
// addressable packages with independently re-rooted artifacts. Language and
// ecosystem stay in each artifact's derived .zpkg.toml and are enforced by the
// per-app lifecycle jobs during install; package/version HTTP metadata does not
// currently duplicate those fields, so this suite tests the contract that does
// exist rather than inventing a UI tag contract.
test.describe("polyglot-lib fans out to four distinct packages", () => {
  const identity = readIdentity(POLYGLOT_FIXTURE);

  test.beforeAll(async () => {
    await ensureSeeded();
  });

  for (const slice of polyglotSlices()) {
    const slug = `${identity.org}/${identity.name}-${slice.suffix}`;

    test(`${slug} has its own page (${slice.ecosystem} slice)`, async ({ page }) => {
      await page.goto(`${WEB_URL}/p/${slug}`);

      await expect(page.locator(".snippet")).toContainText(`zed add ${slug}`);
      await expect(page.locator("table.versions")).toContainText(identity.version);
    });

    test(`${slug} is addressable and pins one artifact`, async ({ request }) => {
      const res = await request.get(
        `${API_URL}/v1/packages/${slug}/versions/${identity.version}`,
      );
      expect(res.status()).toBe(200);

      // Version metadata carries immutable identity/hash fields; language and
      // ecosystem are deliberately verified after install from the artifact's
      // derived manifest by the fixture repos' lifecycle workflows.
      const meta = await res.json();
      expect(meta.name).toBe(`${identity.name}-${slice.suffix}`);
      expect(meta.sha256).toMatch(/^[0-9a-f]{64}$/);
      expect(meta.yanked).toBe(false);
    });
  }

  test("the four slices are four separate packages, not four versions of one", async ({
    request,
  }) => {
    const slugs = polyglotSlices().map(
      (slice) => `${identity.org}/${identity.name}-${slice.suffix}`,
    );

    const shas = new Set<string>();
    for (const slug of slugs) {
      const res = await request.get(
        `${API_URL}/v1/packages/${slug}/versions/${identity.version}`,
      );
      expect(res.status()).toBe(200);
      shas.add((await res.json()).sha256);
    }

    // Distinct content hashes prove each artifact was re-rooted at its own
    // subtree; identical hashes would mean the whole repo shipped four times.
    expect(shas.size).toBe(POLYGLOT_FIXTURE.expectedArtifacts);
  });

  test("the unsuffixed name is not itself a package", async ({ page }) => {
    // `polyglot-lib` is the repo, not a package. If the registry also served it
    // as a package, a consumer could install an untagged everything-bundle and
    // the per-ecosystem refusal would have nothing to refuse.
    const res = await page.goto(`${WEB_URL}/p/${identity.org}/${identity.name}`);
    expect(res?.status()).toBe(404);
  });
});
