import { defineConfig, devices } from "@playwright/test";

// End-to-end tests of the pipeline editor in real browsers.  Own backend (fresh DB, MQTT off) on :5190 and web app on :5290; never the real app.
const API = "http://127.0.0.1:5190", WEB = "http://127.0.0.1:5290";
export default defineConfig({
  testDir: "./e2e",
  testIgnore: /screenshots.spec.ts/,
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: WEB, trace: "retain-on-failure", viewport: { width: 1600, height: 1000 } },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1600, height: 1000 } } },
    { name: "webkit", use: { ...devices["Desktop Safari"], viewport: { width: 1600, height: 1000 } } },
  ],
  webServer: [
    { command: "node e2e/start-backend.mjs", url: `${API}/api/health`, reuseExistingServer: false, timeout: 60_000, stdout: "ignore", stderr: "ignore" },
    { command: "npx vite --host 127.0.0.1 --port 5290 --strictPort", url: WEB, reuseExistingServer: false, timeout: 60_000, env: { VITE_API_BASE: `${API}/api` } },
  ],
});
