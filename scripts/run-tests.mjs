// Runs the backend test-suite and the dashboard build (type-check free build).
import { spawnSync } from "node:child_process";
import { join } from "node:path";
import { BACKEND, APP, VENV_PY, fail, requireVenv } from "./lib.mjs";
requireVenv();
let r = spawnSync(VENV_PY, ["-m", "pytest", "-q"], { cwd: BACKEND, stdio: "inherit" });
if (r.status !== 0) fail("backend tests failed");
r = spawnSync("npm", ["run", "build"], { cwd: APP, stdio: "inherit", shell: process.platform === "win32" });
if (r.status !== 0) fail("dashboard build failed");

// Web app unit + component tests (vitest: QR vectors, pipeline model, editor, pages)
r = spawnSync("npm", ["test"], { cwd: APP, stdio: "inherit", shell: process.platform === "win32" });
if (r.status !== 0) fail("web app tests failed");

// Tools tests (QR parser shared vectors, sticker generator, secrets generator)
r = spawnSync(VENV_PY, ["-m", "pytest", "-q", "tools/tests"], { cwd: join(BACKEND, "..", ".."), stdio: "inherit" });
if (r.status !== 0) fail("tools tests failed");

// Firmware protocol + pairing tests (host-compiled real library code). Skipped when no C++ compiler is installed.
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { ROOT } from "./lib.mjs";
const cxx = ["clang++", "g++"].find((c) => spawnSync(c, ["--version"], { stdio: "ignore", shell: process.platform === "win32" }).status === 0);
if (!cxx) {
  console.log("[tests] no C++ compiler found - firmware host tests SKIPPED");
} else {
  const out = mkdtempSync(join(tmpdir(), "aqua-fw-"));
  for (const t of ["test_aqua", "test_join", "test_gateway"]) {
    const exe = join(out, t + (process.platform === "win32" ? ".exe" : ""));
    r = spawnSync(cxx, ["-std=c++17", "-Ilibraries/AquaProtocol/src", "-Itests/firmware", `tests/firmware/${t}.cpp`, "-o", exe], { cwd: ROOT, stdio: "inherit" });
    if (r.status !== 0) fail(`${t} did not compile`);
    r = spawnSync(exe, [], { cwd: ROOT, stdio: "inherit" });
    if (r.status !== 0) fail(`${t} failed`);
  }
}

// Gateway portal: JS QR parser against the shared vectors + build (syntax check, size budget)
r = spawnSync("node", ["scripts/test_portal_qr.mjs"], { cwd: ROOT, stdio: "inherit" });
if (r.status !== 0) fail("portal QR tests failed");
r = spawnSync("node", ["scripts/test_portal_ui.mjs"], { cwd: ROOT, stdio: "inherit" });   // jsdom UI regression (SKIPPED when jsdom is missing)
if (r.status !== 0) fail("portal UI tests failed");
r = spawnSync("node", ["scripts/build_portal.mjs"], { cwd: ROOT, stdio: "inherit" });
if (r.status !== 0) fail("portal build failed");

// Real-browser editor tests (Playwright: Chromium + WebKit) against a throw-away backend and database. SKIPPED with a message when the browsers are not installed.
import { existsSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
const pwDir = process.env.PLAYWRIGHT_BROWSERS_PATH || (process.platform === "win32" ? join(process.env.LOCALAPPDATA || homedir(), "ms-playwright") : process.platform === "darwin" ? join(homedir(), "Library", "Caches", "ms-playwright") : join(homedir(), ".cache", "ms-playwright"));
const have = (p) => existsSync(pwDir) && readdirSync(pwDir).some((d) => d.startsWith(p));
const missing = ["chromium", "webkit"].filter((b) => !have(b));
if (missing.length) {
  console.log(`[tests] Playwright browser(s) not installed (${missing.join(", ")}) - editor end-to-end tests SKIPPED. Install with:  cd Aqua-Guard-WCS-UI && npx playwright install ${missing.join(" ")}`);
} else {
  r = spawnSync("npx", ["playwright", "test"], { cwd: APP, stdio: "inherit", shell: process.platform === "win32" });
  if (r.status !== 0) fail("editor end-to-end tests failed");
}
