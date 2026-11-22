import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  // The live free-tier server is far slower than a laptop; allow more time when testing a deployment.
  timeout: process.env.E2E_BASE_URL ? 1_200_000 : 240_000,
  expect: { timeout: process.env.E2E_BASE_URL ? 60_000 : 20_000 },
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  outputDir: "./e2e-results",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
});
