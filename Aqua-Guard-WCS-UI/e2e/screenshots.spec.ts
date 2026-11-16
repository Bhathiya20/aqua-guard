import { expect, request, test, type Page } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const EMAIL = "e2e@aquaguard.local", PASSWORD = "E2e.Aqua1x";
const OUT = join(process.cwd(), "..", "docs", "screenshots");
const SIZES = [{ name: "1440", width: 1440, height: 900 }, { name: "375", width: 375, height: 812 }];
const PAGES: Array<[string, string]> = [
  ["overview", "/"], ["water-monitoring", "/water-monitoring"], ["leak-detection", "/leak-detection"], ["rainwater", "/rainwater"], ["nodes", "/nodes"],
  ["pipelines", "/pipelines"], ["pipeline-editor", "/pipelines/1"], ["pipeline-live", "/pipelines/1?mode=live"], ["legacy", "/legacy"],
  ["analytics", "/analytics"], ["alerts", "/alerts"], ["reports", "/reports"], ["admin", "/admin"], ["profile", "/profile"],
];
const report: Record<string, Record<string, { overflowPx: number; wideElements: string[] }>> = {};

async function login(page: Page) {
  await page.goto("/");
  await page.getByLabel(/email/i).fill(EMAIL);
  await page.getByLabel(/password/i).fill(PASSWORD);
  await page.getByRole("button", { name: /sign in|log in|login/i }).click();
  await expect(page.getByRole("link", { name: "Pipeline", exact: true }).or(page.getByRole("button", { name: /toggle menu/i }))).toBeVisible();
}

for (const sz of SIZES) {
  test(`screenshots at ${sz.name} px`, async ({ page }) => {
    mkdirSync(join(OUT, sz.name), { recursive: true });
    await page.setViewportSize({ width: sz.width, height: sz.height });
    await page.goto("/");
    await expect(page.getByLabel(/email/i)).toBeVisible();
    await page.screenshot({ path: join(OUT, sz.name, "login.png"), fullPage: true });
    await login(page);
    // make sure there is an active pipeline (the example house) so the pipeline pages have content
    const api = await request.newContext({ baseURL: "http://127.0.0.1:5190" });
    const lg = await (await api.post("/api/login", { data: { email: EMAIL, password: PASSWORD } })).json();
    const hdr = { "X-CSRF-Token": lg.csrfToken };
    let list = await (await api.get("/api/pipelines")).json();
    if (list.pipelines.length === 0) {
      const doc = JSON.parse(readFileSync(join(process.cwd(), "examples", "four-node-house.json"), "utf8"));
      const r = await api.post("/api/pipelines", { headers: hdr, data: { name: "Four-node house", doc } });
      expect(r.ok()).toBeTruthy();
      list = await (await api.get("/api/pipelines")).json();
    }
    const pid = (list.pipelines.find((p: { activeVersion: number | null }) => p.activeVersion) ?? list.pipelines[0]).id;
    for (const [name, url0] of PAGES) {
      const url = url0.replace("/pipelines/1", `/pipelines/${pid}`);
      await page.goto(url);
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(1800);                                  // polling pages need a moment to fill
      const m = await page.evaluate(() => {
        const de = document.documentElement, vw = de.clientWidth;
        const wide = Array.from(document.querySelectorAll<HTMLElement>("main *")).filter((e) => {
          const r = e.getBoundingClientRect();
          if (r.width === 0 || r.right <= vw + 1) return false;
          // ignore anything inside a deliberately scrollable box
          for (let p: HTMLElement | null = e.parentElement; p && p.tagName !== "MAIN"; p = p.parentElement) { const o = getComputedStyle(p).overflowX; if (o === "auto" || o === "scroll" || o === "hidden") return false; }
          return true;
        }).slice(0, 4).map((e) => `${e.tagName.toLowerCase()}${e.className && typeof e.className === "string" ? "." + e.className.split(" ").slice(0, 2).join(".") : ""} right=${Math.round(e.getBoundingClientRect().right)}`);
        return { overflowPx: Math.max(0, de.scrollWidth - vw), wideElements: wide };
      });
      (report[sz.name] ??= {})[name] = m;
      await page.screenshot({ path: join(OUT, sz.name, `${name}.png`), fullPage: name !== "pipeline-editor" && name !== "pipeline-live" });
    }
    writeFileSync(join(OUT, `overflow-${sz.name}.json`), JSON.stringify(report[sz.name], null, 2));
  });
}
