import { expect, test, type Page } from "@playwright/test";

// The "..." menu in the Actions column of the Nodes page, in a real browser: every entry must open its dialog (or act), and the page must stay usable afterwards.
const EMAIL = "e2e@aquaguard.local", PASSWORD = "E2e.Aqua1x";

async function login(page: Page) {
  await page.goto("/");
  await page.getByLabel(/email/i).fill(EMAIL);
  await page.getByLabel(/password/i).fill(PASSWORD);
  await page.getByRole("button", { name: /sign in|log in|login/i }).click();
  await expect(page.getByRole("link", { name: "Pipeline", exact: true })).toBeVisible();
}

/** Open the "..." menu of the first flow node row and return its number. */
async function openMenu(page: Page) {
  const btn = page.locator('[data-testid^="row-menu-"]:visible').first();
  await expect(btn).toBeVisible();
  const id = (await btn.getAttribute("data-testid"))!;
  await btn.click();
  await expect(page.getByRole("menu")).toBeVisible();
  return id.replace("row-menu-", "");
}

/** After a dialog has been used the page must accept clicks again (a stuck modal layer / pointer-events:none on <body> would not). */
async function pageStillUsable(page: Page) {
  await expect(page.locator("[role=dialog], [role=alertdialog]")).toHaveCount(0);
  await expect.poll(async () => page.evaluate(() => getComputedStyle(document.body).pointerEvents)).not.toBe("none");
  await page.locator('[data-testid^="row-menu-"]:visible').first().click();
  await expect(page.getByRole("menu")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("menu")).toHaveCount(0);
}

const ACTIONS: { name: RegExp; dialog: RegExp }[] = [
  { name: /^Rename$/, dialog: /^Rename /i },
  { name: /^Calibrate$/, dialog: /^Calibrate /i },
  { name: /^Usage settings$/, dialog: /usage/i },
  { name: /^TX power$/, dialog: /TX power/i },
  { name: /^Replace$/, dialog: /Replace /i },
  { name: /^Remove$/, dialog: /Remove /i },
];

test.describe("Nodes page: the ... action menu", () => {
  test.beforeEach(async ({ page }) => { await login(page); await page.goto("/nodes"); await expect(page.getByTestId("gateway-card")).toBeVisible(); });

  for (const a of ACTIONS) {
    test(`"${a.name.source.replace(/[\\^$/]/g, "")}" opens its dialog, closes again and leaves the page usable`, async ({ page }) => {
      await openMenu(page);
      await page.getByRole("menuitem", { name: a.name }).click();
      const dlg = page.getByRole("dialog").or(page.getByRole("alertdialog"));      // Remove is a confirmation (alertdialog)
      await expect(dlg).toBeVisible();
      await expect(dlg.getByRole("heading").first()).toHaveText(a.dialog);
      await page.keyboard.press("Escape");
      await pageStillUsable(page);
    });
  }

  test('"Out of service / maintenance" toggles the node and the menu then offers "Back in service"', async ({ page }) => {
    const n = await openMenu(page);
    await page.getByRole("menuitem", { name: /Out of service \/ maintenance/ }).click();
    await expect(page.getByTestId(`node-row-${n}`)).toContainText(/out of service/i);       // also while the gateway is offline (state "unknown")
    await page.locator(`[data-testid="row-menu-${n}"]:visible`).click();
    await expect(page.getByRole("menuitem", { name: /Back in service/ })).toBeVisible();
    await page.getByRole("menuitem", { name: /Back in service/ }).click();
    await expect(page.getByTestId(`node-row-${n}`)).not.toContainText(/out of service/i);
  });

  test("the menu also works on a phone-width screen (cards)", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 800 });
    await page.goto("/nodes");
    const btn = page.locator('[data-testid^="row-menu-"]:visible').first();
    await btn.scrollIntoViewIfNeeded();
    await btn.click();
    await page.getByRole("menuitem", { name: /^Rename$/ }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
  });
});
