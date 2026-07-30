import { expect, test } from "@playwright/test";
import { SINGLE_LANGUAGE_FIXTURES, WEB_URL } from "../harness/stack.js";
import { ensureSeeded, readIdentity } from "../harness/fixtures.js";

// What the per-repo CI cannot check: that a fixture published from its real tree
// is then *rendered correctly by the registry UI*. The repo CI stops at "the
// package is well-formed"; this starts at "a human opening the page sees the
// truth", in a real browser.
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

    test(`${slug} page agrees with its .zpkg.toml`, async ({ page }) => {
      await page.goto(`${WEB_URL}/p/${slug}`);
      const body = page.locator("body");

      // Description and license come from the manifest the fixture repo owns;
      // if the UI shows something else, one of the two drifted.
      await expect(body).toContainText(identity.description);
      await expect(body).toContainText(identity.license);
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
