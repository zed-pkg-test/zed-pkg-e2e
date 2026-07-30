# zed-pkg-e2e

Browser automation over the **zed-pkg-test fixture packages**.

## How this differs from `zed-pkg/zed-e2e`

[`zed-pkg/zed-e2e`](https://github.com/zed-pkg/zed-e2e) is the registry stack's
own end-to-end suite: it boots Postgres and both Rust servers, seeds *synthetic*
packages (`acme/http-kit` and friends) written to temp dirs, and drives the whole
system with Playwright, Puppeteer and Selenium. That is the right shape for
testing registry semantics.

This repo covers the gap that leaves: the fixture repos in this org are published
by their own CI, and nothing then checks **what the registry UI shows for them**.
Here the inputs are the real fixture trees — the same ones `zed publish` runs
against in each repo — so what the browser asserts is what a consumer would see.

| Suite | What it covers |
| --- | --- |
| `suites/fixture-package-pages.spec.ts` | Each single-language fixture (`node-lib`, `rust-lib`, `go-lib`, `python-lib`) renders the version, description, license and install snippet its `.zpkg.toml` declares; each is findable via HTMX search; a missing package is a real 404 |
| `suites/polyglot-fan-out.spec.ts` | `polyglot-lib` becomes four separately-addressable packages, each tagged with its own `language`/`ecosystem`, each with a distinct content hash — and the unsuffixed repo name is *not* a package |

It deliberately does **not** re-implement stack orchestration. A second copy of
that boot sequence would be one more thing to keep in sync for no added
coverage, so this suite attaches to a stack `zed-e2e` brought up.

## Run

```bash
# 1. bring the stack up using the suite that owns it
cd ../zed-e2e && npm run stack:up && cd -

# 2. this repo's prerequisites
npm install
npx playwright install chromium

# 3. drive the fixtures through a browser
npm run e2e
npm run e2e:fixtures     # single-language package pages only
npm run e2e:polyglot     # the four-slice fan-out only
```

Sibling checkouts are expected alongside this one: `zed-cli` (for the `zed`
binary), and the fixture repos it publishes — `node-lib`, `rust-lib`, `go-lib`,
`python-lib`, `polyglot-lib`.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `ZED_E2E_API_URL` | `http://127.0.0.1:48080` | API server to assert metadata against |
| `ZED_E2E_WEB_URL` | `http://127.0.0.1:48081` | Web server the browser drives |
| `ZED_BIN` | `../zed-cli/target/debug/zed` | `zed` binary used to publish the fixtures |
| `PW_CONNECT_WS` | — | Drive a remote Playwright browser server instead of a local Chromium |

Defaults match `zed-e2e`'s, so the common local case needs no environment at all.

## License

MIT
