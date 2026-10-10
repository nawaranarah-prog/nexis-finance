import { defineConfig, devices } from "@playwright/test";
import { join } from "node:path";

/* One local stack for every suite: the production build of the website (vite preview) and the FastAPI backend on a
   throwaway SQLite database. The backend's outbound HTTP goes to a dead proxy, so no test can reach a data provider,
   an AI provider or Stripe; public data comes from recorded fixtures (tests/support/mockApi.ts). */

const BACKEND = join(import.meta.dirname, "..", "backend");
const PY = `"${process.platform === "win32" ? join(BACKEND, ".venv", "Scripts", "python.exe") : join(BACKEND, ".venv", "bin", "python")}"`;
const DB = join(import.meta.dirname, "test-results", "e2e.db").replace(/\\/g, "/");
const DEAD_PROXY = "http://127.0.0.1:9";

export default defineConfig({
  testDir: "./tests",
  testIgnore: ["**/unit/**"],
  outputDir: "./test-results/artifacts",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: true,
  workers: process.env.CI ? 2 : 4,
  retries: 0,
  reporter: [["list"], ["json", { outputFile: "test-results/results.json" }]],
  use: { baseURL: "http://localhost:4173", trace: "retain-on-failure", serviceWorkers: "block" },
  projects: [
    { name: "chromium", testMatch: /e2e\/.*\.spec\.ts/, use: { ...devices["Desktop Chrome"] } },
    { name: "firefox", testMatch: /e2e\/.*\.spec\.ts/, use: { ...devices["Desktop Firefox"] } },
    { name: "webkit", testMatch: /e2e\/.*\.spec\.ts/, use: { ...devices["Desktop Safari"] } },
    { name: "responsive", testMatch: /responsive\/.*\.spec\.ts/, use: { ...devices["Desktop Chrome"] } },
    { name: "a11y", testMatch: /a11y\/.*\.spec\.ts/, use: { ...devices["Desktop Chrome"] } },
    { name: "live-smoke", testMatch: /live\/.*\.spec\.ts/, use: { ...devices["Desktop Chrome"] } },
    { name: "links", testMatch: /links\/.*\.spec\.ts/, use: { ...devices["Desktop Chrome"] } },
    { name: "record", testMatch: /record\/.*\.spec\.ts/, use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: [
    {
      command: `${PY} -c "import os; p='${DB}'; os.path.exists(p) and os.remove(p)" && ${PY} -m uvicorn app.main:app --port 8010`,
      cwd: BACKEND,
      url: "http://127.0.0.1:8010/api/health",
      timeout: 180_000,
      reuseExistingServer: false,
      env: {
        NEXIS_DATABASE_URL: `sqlite:///${DB}`, NEXIS_ENV: "development", NEXIS_LLM_API_KEY: "", AI_GATEWAY_API_KEY: "",
        STRIPE_SECRET_KEY: "", NEXIS_STRIPE_SECRET_KEY: "",
        NEXIS_WIKIDATA_FIXTURES_DIR: join(BACKEND, "tests", "fixtures"),  // World Graph: recorded Wikidata answers, no network
        HTTP_PROXY: DEAD_PROXY, HTTPS_PROXY: DEAD_PROXY, ALL_PROXY: DEAD_PROXY, NO_PROXY: "127.0.0.1,localhost",
      },
    },
    {
      command: "npx vite build && npx vite preview --port 4173 --strictPort",
      url: "http://localhost:4173",
      timeout: 240_000,
      reuseExistingServer: false,
      env: { VITE_API_PROXY: "http://127.0.0.1:8010" },
    },
  ],
});
