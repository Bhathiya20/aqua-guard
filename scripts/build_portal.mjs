// Build the gateway portal: inline qr.js into the page, gzip the files into firmware/gateway/data/, build a LittleFS image
// and optionally flash it to the gateway's LittleFS partition (0x200000, 0x1F0000 bytes; see firmware/gateway/partitions.csv).
//
//   node scripts/build_portal.mjs                    build + size check
//   node scripts/build_portal.mjs --flash COM5       build + write the LittleFS image to the gateway on COM5
// Close any serial monitor first (the port must be free).
import { mkdirSync, readFileSync, writeFileSync, existsSync, rmSync } from "node:fs";
import { join } from "node:path";
import { gzipSync } from "node:zlib";
import { spawnSync } from "node:child_process";
import vm from "node:vm";
import { ROOT, fail } from "./lib.mjs";
import { TC } from "./setup_firmware_toolchain.mjs";

const P = join(ROOT, "firmware/gateway/portal"), DATA = join(ROOT, "firmware/gateway/data"), BUILD = join(ROOT, "firmware/gateway/build");
const FS_OFFSET = "0x200000", FS_SIZE = 0x1F0000, BUDGET = 150 * 1024;

let html = readFileSync(join(P, "index.src.html"), "utf8");
const qr = readFileSync(join(P, "qr.js"), "utf8");
if (!html.includes("/*@@QR@@*/")) fail("placeholder /*@@QR@@*/ missing in index.src.html");
html = html.replace("/*@@QR@@*/", () => qr);
// syntax check of every inline script (a typo here would only show up on the phone otherwise)
for (const m of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) { try { new vm.Script(m[1]); } catch (e) { fail("portal script syntax error: " + e.message); } }

rmSync(DATA, { recursive: true, force: true }); mkdirSync(DATA, { recursive: true }); mkdirSync(BUILD, { recursive: true });
const files = { "index.html.gz": gzipSync(Buffer.from(html), { level: 9 }), "jsqr.js.gz": gzipSync(readFileSync(join(P, "vendor/jsQR.js")), { level: 9 }) };
let total = 0;
for (const [n, b] of Object.entries(files)) { writeFileSync(join(DATA, n), b); total += b.length; console.log(`  ${n}: ${(b.length / 1024).toFixed(1)} KB`); }
console.log(`portal total ${(total / 1024).toFixed(1)} KB gzipped (budget ${BUDGET / 1024} KB)`);
if (total > BUDGET) fail("portal exceeds the size budget");

const win = process.platform === "win32";
const tools = join(TC, "data/packages/esp32/tools");
const find = (dir, exe) => { const r = spawnSync(win ? "cmd" : "ls", win ? ["/c", "dir", "/b", "/s", join(dir, exe + ".exe")] : [join(dir, "*", exe)], { encoding: "utf8", shell: !win }); return (r.stdout || "").split(/\r?\n/)[0].trim(); };
const mkfs = find(join(tools, "mklittlefs"), "mklittlefs"), esptool = find(join(tools, "esptool_py"), "esptool");
if (!mkfs || !existsSync(mkfs)) fail("mklittlefs not found: run node scripts/setup_firmware_toolchain.mjs");
const img = join(BUILD, "littlefs.bin");
let r = spawnSync(mkfs, ["-c", DATA, "-p", "256", "-b", "4096", "-s", String(FS_SIZE), img], { stdio: "inherit" });
if (r.status !== 0) fail("mklittlefs failed");
console.log(`LittleFS image: ${img}`);

const i = process.argv.indexOf("--flash");
if (i > 0) {
  const port = process.argv[i + 1]; if (!port) fail("--flash needs a port, e.g. --flash COM5");
  if (!esptool) fail("esptool not found: run node scripts/setup_firmware_toolchain.mjs");
  r = spawnSync(esptool, ["--chip", "esp32", "--port", port, "--baud", "460800", "write-flash", FS_OFFSET, img], { stdio: "inherit" });
  if (r.status !== 0) fail("flashing the portal failed");
  console.log("portal flashed. Reset the gateway (or just reload the page).");
}
