import { defineConfig } from "@playwright/test";

// Matches zed-e2e's remote-browser support: PW_CONNECT_WS points Playwright at
// a browser server deployed in a grid instead of launching one locally.
const PW_CONNECT_WS = process.env.PW_CONNECT_WS;

export default defineConfig({
  testDir: "./suites",
  globalSetup: "./harness/global-setup.ts",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  // The registry is shared mutable state and the suites publish into it, so
  // they run serially -- same reasoning as zed-e2e.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.ZED_E2E_WEB_URL ?? "http://127.0.0.1:48081",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...(PW_CONNECT_WS ? { connectOptions: { wsEndpoint: PW_CONNECT_WS } } : {}),
  },
});
