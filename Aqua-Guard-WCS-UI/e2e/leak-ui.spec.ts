import { expect, request, test, type Page } from "@playwright/test";
import { mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

// Leak Detection in a real browser: simple first view -> "More details" -> "Settings", and the demo clock banner on every page.
const EMAIL = "e2e@aquaguard.local", PASSWORD = "E2e.Aqua1x";
const OUT = join(process.cwd(), "..", "docs", "screenshots", "leak-ui");

async function login(page: Page) {
  await page.goto("/");
  await page.getByLabel(/email/i).fill(EMAIL);
  await page.getByLabel(/password/i).fill(PASSWORD);
  await page.getByRole("button", { name: /sign in|log in|login/i }).click();
  await expect(page.getByRole("link", { name: "Pipeline", exact: true })).toBeVisible();
}

async function ensurePipeline() {
  const api = await request.newContext({ baseURL: "http://127.0.0.1:5190" });
  const lg = await (await api.post("/api/login", { data: { email: EMAIL, password: PASSWORD } })).json();
  const hdr = { "X-CSRF-Token": lg.csrfToken };
  const list = await (await api.get("/api/pipelines")).json();
  if (list.pipelines.length === 0) {
    const doc = JSON.parse(readFileSync(join(process.cwd(), "examples", "four-node-house.json"), "utf8"));
    expect((await api.post("/api/pipelines", { headers: hdr, data: { name: "Four-node house", doc } })).ok()).toBeTruthy();
  }
  return { api, hdr };
}

test.describe("Leak Detection: progressive disclosure and demo clock", () => {
  test.beforeEach(async ({ page }) => { mkdirSync(OUT, { recursive: true }); await ensurePipeline(); await login(page); await page.goto("/leak-detection"); await expect(page.getByTestId("leak-overall")).toBeVisible(); });

  test("first view -> More details -> Settings", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const card = page.locator('[data-testid^="leak-zone-"]').first();
    await expect(card).toBeVisible();
    await expect(page.locator('[data-testid^="zone-details-"]')).toHaveCount(0);            // collapsed
    await expect(page.getByTestId("leak-node-table")).toHaveCount(0);
    await page.screenshot({ path: join(OUT, "1-first-view.png"), fullPage: true });

    const monitored = page.locator('[data-testid^="zone-more-zone:flow"]').first();
    await monitored.click();
    await expect(page.locator('[data-testid^="zone-details-"]').first()).toBeVisible();
    await page.screenshot({ path: join(OUT, "2-more-details.png"), fullPage: true });

    await page.locator('[data-testid^="zone-settings-btn-"]').first().click();
    const dlg = page.getByRole("dialog");
    await expect(dlg).toBeVisible();
    await expect(dlg.locator('[data-testid^="time-settings-"]')).toContainText(/Quiet hours start/);
    await page.screenshot({ path: join(OUT, "3-settings.png") });
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
  });

  test("the demo clock shows a banner on every page and switches back", async ({ page }) => {
    await page.getByTestId("section-toggle-demo").click();
    await page.getByTestId("clock-night").click();
    await expect(page.getByTestId("clock-banner")).toContainText("NIGHT (simulated)");
    await page.screenshot({ path: join(OUT, "4-demo-clock-night.png") });
    await page.goto("/nodes");
    await expect(page.getByTestId("clock-banner")).toBeVisible();                          // on another page too
    await page.goto("/leak-detection");
    await page.getByTestId("section-toggle-demo").click();
    await page.getByTestId("clock-auto").click();
    await expect(page.getByTestId("clock-banner")).toHaveCount(0);
  });
});
