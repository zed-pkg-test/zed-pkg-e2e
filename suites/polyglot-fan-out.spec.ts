import { expect, test } from "@playwright/test";
import { API_URL, POLYGLOT_FIXTURE, WEB_URL } from "../harness/stack.js";
import { ensureSeeded, polyglotSlices, readIdentity } from "../harness/fixtures.js";

// The polyglot claim is that ONE repo becomes FOUR independently installable
// packages, each tagged with the ecosystem it belongs to. That is the assertion
// worth making through the UI and the API together: a visitor must be able to
// tell the four slices apart, or the ecosystem-mismatch refusal that depends on
// those tags is unexplainable.
test.describe("polyglot-lib fans out to four distinct packages", () => {
  const identity = readIdentity(POLYGLOT_FIXTURE);

  test.beforeAll(async () => {
    await ensureSeeded();
  });

  for (const slice of polyglotSlices()) {
    const slug = `${identity.org}/${identity.name}-${slice.suffix}`;

    test(`${slug} has its own page tagged ${slice.ecosystem}`, async ({ page }) => {
      await page.goto(`${WEB_URL}/p/${slug}`);

      await expect(page.locator(".snippet")).toContainText(`zed add ${slug}`);
      await expect(page.locator("table.versions")).toContainText(identity.version);
      // The language/ecosystem tags are what make the wrong-package install
      // refusable, so they have to be visible, not merely stored.
      await expect(page.locator("body")).toContainText(slice.ecosystem);
    });

    test(`${slug} metadata declares language ${slice.language}`, async ({ request }) => {
      const res = await request.get(
        `${API_URL}/v1/packages/${slug}/versions/${identity.version}`,
      );
      expect(res.status()).toBe(200);

      const meta = await res.json();
      expect(meta.language).toBe(slice.language);
      expect(meta.ecosystem).toBe(slice.ecosystem);
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
