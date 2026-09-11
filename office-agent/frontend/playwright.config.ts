import { defineConfig } from "@playwright/test";

// No mocks, no auto-started webServer: these specs run against a real,
// separately-running coscribe-web (real LLM calls) and a real `vite
// dev` proxy, matching this project's own live-verify discipline (see
// README.md's dev-loop section for how to start both). Point
// PLAYWRIGHT_BASE_URL elsewhere if the frontend isn't on the default port.
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 45_000,
  // Real Gemini calls, back to back across specs, occasionally hit 429s
  // that the backend retries with its own backoff (see runtime/gemini
  // retry helper) -- a tight default expect() timeout flakes on that,
  // not on anything actually broken. Individual assertions that chain
  // two model calls (e.g. a tool call's own follow-up summary turn)
  // still set an even longer explicit timeout on top of this.
  expect: { timeout: 15_000 },
  fullyParallel: false,
  // All specs share one real dev backend process (no mocks) -- running
  // them concurrently makes real LLM/tool-call turns contend with each
  // other on that single process, which shows up as flaky timeouts on
  // the slower round trips (persona selection, tool approval). A local
  // smoke suite doesn't need parallel speed badly enough to trade away
  // that reliability.
  workers: 1,
  retries: 0,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:5173",
    launchOptions: {
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH ?? "/opt/pw-browsers/chromium",
    },
    screenshot: "only-on-failure",
  },
});
