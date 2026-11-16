import { defineConfig, devices } from "@playwright/test";

// Screenshots of every page at 1440 px and 375 px (own backend :5190 with a simulated house, own DB; never the real app).
//   npx playwright test -c playwright.shots.config.ts
const API = "http://127.0.0.1:5190", WEB = "http://127.0.0.1:5290";
export default defineConfig({
  testDir: "./e2e",
  testMatch: /screenshots\.spec\.ts/,
  timeout: 120_000,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: WEB, ...devices["Desktop Chrome"] },
  webServer: [
    { command: "node e2e/start-backend.mjs", url: `${API}/api/health`, reuseExistingServer: false, timeout: 60_000, stdout: "ignore", stderr: "ignore" },
    { command: "npx vite --host 127.0.0.1 --port 5290 --strictPort", url: WEB, reuseExistingServer: false, timeout: 60_000, env: { VITE_API_BASE: `${API}/api` } },
  ],
});
