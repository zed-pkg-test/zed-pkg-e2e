import { expect, test } from "@playwright/test";
import { SINGLE_LANGUAGE_FIXTURES, WEB_URL } from "../harness/stack.js";
import { ensureSeeded, readIdentity } from "../harness/fixtures.js";

// What the per-repo CI cannot check: that a fixture published from its real tree
// is then *rendered correctly by the registry UI*. The repo CI stops at "the
// package is well-formed"; this starts at "a human opening the page sees the
// persisted registry metadata", in a real browser.
test.describe("fixture packages render on the registry UI", () => {
  test.beforeAll(async () => {
    await ensureSeeded();
  });

  for (const fixture of SINGLE_LANGUAGE_FIXTURES) {
    const identity = readIdentity(fixture);
    const slug = `${identity.org}/${identity.name}`;

    test(`${slug} page shows the version and install snippet it published`, async ({ page }) => {
      await page.goto(`${WEB_URL}/p/${slug}`);

      // The install snippet is the one string a visitor copies, so a wrong org
      // or name here is the most consequential rendering bug on the page.
      await expect(page.locator(".snippet")).toContainText(`zed add ${slug}`);
      await expect(page.locator("table.versions")).toContainText(identity.version);
    });

    test(`${slug} page agrees with persisted manifest metadata`, async ({ page }) => {
      await page.goto(`${WEB_URL}/p/${slug}`);
      const body = page.locator("body");

      // Description and repository URL are carried in package metadata and
      // rendered by the web server. License remains inside the immutable
      // artifact manifest today, so asserting it here would invent a web/API
      // contract that does not exist.
      await expect(body).toContainText(identity.description);
      await expect(body).toContainText(identity.repositoryUrl);
    });
  }

  test("every fixture is reachable by search", async ({ page }) => {
    await page.goto(`${WEB_URL}/search`);

    for (const fixture of SINGLE_LANGUAGE_FIXTURES) {
      const identity = readIdentity(fixture);
      await page.fill("#q", identity.name);
      // htmx swaps the fragment into #results on debounced keyup.
      await expect(page.locator("#results")).toContainText(identity.name, {
        timeout: 10_000,
      });
    }
  });

  test("an unpublished package under the fixture org is a 404, not an empty page", async ({
    page,
  }) => {
    // Guards against the UI rendering a blank shell for anything typed into the
    // URL bar, which would make a real missing package indistinguishable from a
    // published one whose metadata failed to load.
    const res = await page.goto(`${WEB_URL}/p/zed-pkg-test/no-such-lib`);
    expect(res?.status()).toBe(404);
  });
});
