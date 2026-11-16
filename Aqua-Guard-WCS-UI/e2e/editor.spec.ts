import { expect, test, type Locator, type Page } from "@playwright/test";

const EMAIL = "e2e@aquaguard.local", PASSWORD = "E2e.Aqua1x";

async function login(page: Page) {
  await page.goto("/");
  await page.getByLabel(/email/i).fill(EMAIL);
  await page.getByLabel(/password/i).fill(PASSWORD);
  await page.getByRole("button", { name: /sign in|log in|login/i }).click();
  await expect(page.getByRole("link", { name: "Pipeline", exact: true })).toBeVisible();
}

async function newPipeline(page: Page, name: string) {
  await page.goto("/pipelines");
  await page.getByRole("button", { name: /new pipeline/i }).click();
  await page.getByLabel("Name").fill(name);
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByTestId("canvas")).toBeVisible();
  await expect(page.getByTestId("palette")).toBeVisible();
}

const parts = (page: Page) => page.locator('[data-testid^="part-"]');
const edges = (page: Page) => page.locator(".react-flow__edge");
const canvas = (page: Page) => page.getByTestId("canvas");

async function box(l: Locator) {
  const b = await l.boundingBox();
  if (!b) throw new Error("element has no box");
  return b;
}
const centre = (b: { x: number; y: number; width: number; height: number }) => ({ x: b.x + b.width / 2, y: b.y + b.height / 2 });

/** Place a palette item by drag-and-drop at a point inside the canvas (offset from the canvas top-left). */
async function dropFromPalette(page: Page, key: string, dx: number, dy: number) {
  // Playwright cannot drive HTML5 drag-and-drop in WebKit; phones use tap-to-place anyway, so WebKit taps.
  if (test.info().project.name === "webkit") { await page.getByTestId(`palette-${key}`).click(); await canvas(page).click({ position: { x: dx, y: dy } }); return; }
  await page.getByTestId(`palette-${key}`).dragTo(canvas(page), { targetPosition: { x: dx, y: dy } });
}
/** Place by "tap the item, then tap the canvas" (the phone flow). */
async function tapPlace(page: Page, key: string, dx: number, dy: number) {
  await page.getByTestId(`palette-${key}`).click();
  await canvas(page).click({ position: { x: dx, y: dy } });
}

async function drag(page: Page, from: { x: number; y: number }, to: { x: number; y: number }) {
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move((from.x + to.x) / 2, (from.y + to.y) / 2, { steps: 6 });
  await page.mouse.move(to.x, to.y, { steps: 8 });
  await page.mouse.move(to.x, to.y, { steps: 2 });          // React Flow applies a move one event late: settle before releasing
  await page.waitForTimeout(60);
  await page.mouse.up();
}

test.beforeEach(async ({ page }) => { await login(page); });

test("parts dropped from the palette appear and STAY (also after the 2 s / 4 s background refreshes)", async ({ page }) => {
  await newPipeline(page, "e2e drop");
  await dropFromPalette(page, "source", 80, 120);
  await expect(parts(page)).toHaveCount(1);
  await tapPlace(page, "pipe", 260, 120);
  await tapPlace(page, "outlet-tap", 460, 120);
  await expect(parts(page)).toHaveCount(3);
  await page.waitForTimeout(6500);                       // polling refreshes (nodes every 4 s, validation, status) must not drop anything
  await expect(parts(page)).toHaveCount(3);
  for (const p of await parts(page).all()) await expect(p).toBeVisible();   // and none of them may be invisible / outside the viewport
  const area = await box(canvas(page));
  for (const p of await parts(page).all()) {
    const b = await box(p);
    expect(b.x).toBeGreaterThanOrEqual(area.x - 1); expect(b.y).toBeGreaterThanOrEqual(area.y - 1);
    expect(b.x + b.width).toBeLessThanOrEqual(area.x + area.width + 1); expect(b.y + b.height).toBeLessThanOrEqual(area.y + area.height + 1);
  }
});

test("a part can be dragged and keeps its new position", async ({ page }) => {
  await newPipeline(page, "e2e move");
  await tapPlace(page, "pipe", 200, 200);
  const p = parts(page).first();
  const before = centre(await box(p));
  await drag(page, before, { x: before.x + 140, y: before.y + 60 });
  await page.waitForTimeout(800);
  const after = centre(await box(p));
  expect(after.x - before.x).toBeGreaterThan(100);
  expect(after.y - before.y).toBeGreaterThan(40);
  await page.waitForTimeout(4500);
  const later = centre(await box(p));
  expect(Math.abs(later.x - after.x)).toBeLessThan(3);
  await expect(parts(page)).toHaveCount(1);
});

test("dragging from an outlet port dot to an inlet port dot creates a link (magnet off)", async ({ page }) => {
  await newPipeline(page, "e2e connect");
  const magnet = page.getByRole("button", { name: /magnet/i });
  if (await magnet.count()) { if ((await magnet.getAttribute("aria-pressed")) === "true") await magnet.click(); }
  await tapPlace(page, "source", 80, 150);
  await tapPlace(page, "pipe", 300, 150);
  await expect(parts(page)).toHaveCount(2);
  const out = page.getByTestId("handle-src1-out"), inn = page.getByTestId("handle-p1-in");
  await drag(page, centre(await box(out)), centre(await box(inn)));
  await expect(edges(page)).toHaveCount(1);
  await page.waitForTimeout(4500);
  await expect(edges(page)).toHaveCount(1);
});

test("click a port, then click another port also connects (tap-to-connect)", async ({ page }) => {
  await newPipeline(page, "e2e tapconnect");
  await tapPlace(page, "source", 80, 150);
  await tapPlace(page, "pipe", 300, 150);
  await page.getByTestId("handle-src1-out").click();
  await page.getByTestId("handle-p1-in").click();
  await expect(edges(page)).toHaveCount(1);
});

test("rotate, delete, undo, save and reload keep the design", async ({ page }) => {
  await newPipeline(page, "e2e full");
  await tapPlace(page, "source", 80, 150);
  await tapPlace(page, "pipe", 300, 150);
  await tapPlace(page, "outlet-tap", 520, 150);
  await page.getByTestId("handle-src1-out").click(); await page.getByTestId("handle-p1-in").click();
  await page.getByTestId("handle-p1-out").click(); await page.getByTestId("handle-o1-in").click();
  await expect(edges(page)).toHaveCount(2);

  // rotate the pipe: its box turns from wide to tall
  await parts(page).nth(1).click();
  const w0 = await box(parts(page).nth(1));
  await page.getByRole("button", { name: /rotate/i }).click();
  await expect.poll(async () => (await box(parts(page).nth(1))).height).toBeGreaterThan(w0.height * 3);

  // delete the outlet with the keyboard, then undo with Ctrl+Z
  await parts(page).nth(2).click();
  await page.keyboard.press("Delete");
  await expect(parts(page)).toHaveCount(2);
  await page.keyboard.press("Control+z");
  await expect(parts(page)).toHaveCount(3);

  // save and reload
  await page.getByRole("button", { name: /save new version/i }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Save" }).click();
  await expect(page.getByText(/Saved version/)).toBeVisible();
  await page.reload();
  await expect(parts(page)).toHaveCount(3);
  await expect(edges(page)).toHaveCount(2);
  const tall = await box(parts(page).nth(1));
  expect(tall.height).toBeGreaterThan(tall.width);       // the rotation survived the round trip
});

// ---------------------------------------------------------------- magnetic connect
const magnetBtn = (page: Page) => page.getByRole("button", { name: /magnet/i });

test("magnet: dropping a part next to a free port connects it (tap-to-place) and says so", async ({ page }) => {
  await newPipeline(page, "e2e magnet tap");
  await expect(magnetBtn(page)).toHaveAttribute("aria-pressed", "true");            // default ON
  await tapPlace(page, "source", 80, 150);
  await tapPlace(page, "pipe", 170, 150);                                            // ~22 px from the source's outlet
  await expect(edges(page)).toHaveCount(1);
  await expect(page.getByText("Connected", { exact: true }).first()).toBeVisible();
  const o = await box(page.getByTestId("handle-src1-out")), i = await box(page.getByTestId("handle-p1-in"));
  expect(Math.abs(centre(o).x - centre(i).x)).toBeLessThan(6);                       // the ports now touch
  expect(Math.abs(centre(o).y - centre(i).y)).toBeLessThan(6);
});

test("magnet: dropping from the palette by drag-and-drop connects too; far away does not", async ({ page }) => {
  await newPipeline(page, "e2e magnet dnd");
  await tapPlace(page, "source", 80, 150);
  await dropFromPalette(page, "pipe", 170, 150);
  await expect(edges(page)).toHaveCount(1);
  await dropFromPalette(page, "outlet-tap", 560, 300);
  await expect(parts(page)).toHaveCount(3);
  await expect(edges(page)).toHaveCount(1);
});

test("magnet: holding Alt disables it, and so does switching it off (setting is remembered)", async ({ page }) => {
  await newPipeline(page, "e2e magnet off");
  await tapPlace(page, "source", 80, 150);
  await page.getByTestId("palette-pipe").click();
  await canvas(page).click({ position: { x: 170, y: 150 }, modifiers: ["Alt"] });
  await expect(parts(page)).toHaveCount(2);
  await expect(edges(page)).toHaveCount(0);

  await magnetBtn(page).click();
  await expect(magnetBtn(page)).toHaveAttribute("aria-pressed", "false");
  await tapPlace(page, "outlet-tap", 520, 150);
  await tapPlace(page, "pipe", 540, 200);
  await expect(edges(page)).toHaveCount(0);
  await page.reload();
  await expect(magnetBtn(page)).toHaveAttribute("aria-pressed", "false");            // remembered in localStorage
});

test("magnet: dragging a part next to a free port glows both ports and connects on drop; Alt drags freely", async ({ page }) => {
  await newPipeline(page, "e2e magnet drag");
  await tapPlace(page, "source", 80, 150);
  await tapPlace(page, "pipe", 400, 150);
  const p = parts(page).nth(1), from = centre(await box(p)), srcOut = centre(await box(page.getByTestId("handle-src1-out")));
  const target = { x: from.x - 240, y: from.y + 6 };
  await page.mouse.move(from.x, from.y); await page.mouse.down();
  await page.mouse.move((from.x + target.x) / 2, from.y, { steps: 6 });
  await page.mouse.move(target.x, target.y, { steps: 8 });
  await page.mouse.move(target.x + 1, target.y, { steps: 2 });                       // (the drag applies a move one event late)
  await expect(page.locator('[data-snap="1"]')).toHaveCount(2);                      // both ports glow while it is close
  await page.mouse.up();
  await expect(edges(page)).toHaveCount(1);
  void srcOut;

  // pulling it clearly away unplugs it (magnet on) ...
  const q = centre(await box(parts(page).nth(1)));
  await drag(page, q, { x: q.x + 160, y: q.y + 80 });
  await expect(edges(page)).toHaveCount(0);
  // ... and with Alt held it can be dragged right back next to the source without connecting
  const r = centre(await box(parts(page).nth(1)));
  await page.keyboard.down("Alt");
  await drag(page, r, { x: r.x - 160, y: r.y - 80 });
  await page.keyboard.up("Alt");
  await expect(edges(page)).toHaveCount(0);
});

test("magnet: a tiny nudge keeps the link", async ({ page }) => {
  await newPipeline(page, "e2e magnet nudge");
  await tapPlace(page, "source", 80, 150);
  await tapPlace(page, "pipe", 170, 150);
  await expect(edges(page)).toHaveCount(1);
  const q = centre(await box(parts(page).nth(1)));
  await drag(page, q, { x: q.x + 6, y: q.y + 2 });
  await expect(edges(page)).toHaveCount(1);
});

test("magnet: a loop is refused with a reason", async ({ page }) => {
  await newPipeline(page, "e2e magnet loop");
  await magnetBtn(page).click();                                                     // build a pipe -> elbow chain by hand
  await tapPlace(page, "pipe", 200, 100);
  await tapPlace(page, "elbow", 400, 100);
  await page.getByTestId("handle-p1-out").click(); await page.getByTestId("handle-l1-in").click();
  await expect(edges(page)).toHaveCount(1);
  await page.getByTestId("handle-l1-out").click(); await page.getByTestId("handle-p1-in").click();   // would close the circle
  await expect(edges(page)).toHaveCount(1);
  await expect(page.getByText(/circle/i).first()).toBeVisible();
});

test("magnet: a flow node dropped on a pipe snaps onto it", async ({ page }) => {
  await newPipeline(page, "e2e magnet sensor");
  await tapPlace(page, "pipe", 300, 200);
  const nodeItem = page.locator('[data-testid^="palette-node-"]').first();
  test.skip((await nodeItem.count()) === 0, "no unplaced node in the test database");
  await nodeItem.click();
  await canvas(page).click({ position: { x: 322, y: 206 } });
  const m = page.locator('[data-kind="node"]').first();
  await expect(m).toBeVisible();
  await expect(m).not.toHaveAttribute("title", /not on a pipe yet/);
});
