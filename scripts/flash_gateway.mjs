// Flash ONLY the gateway application (0x10000).   node scripts/flash_gateway.mjs COM29
//
// Do NOT use `arduino-cli upload` for the gateway: its recipe also writes boot_app0.bin (8 KB) at 0xE000, but the gateway's partition table
// (firmware/gateway/partitions.csv) has no otadata there: 0xE000-0xEFFF is the LAST PAGE OF THE NVS PARTITION (and 0xF000 is phy_init).  Every upload
// therefore wiped whatever the NVS had stored on that page - on 2026-11-16 the Wi-Fi name, password and "setup done" flag.
// This script compiles, then writes the app image only: bootloader, partition table, NVS and LittleFS stay untouched.
import { existsSync, mkdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { ROOT, fail } from "./lib.mjs";
import { CLI, cliEnv } from "./setup_firmware_toolchain.mjs";

const port = process.argv[2];
if (!/^COM\d+$|^\/dev\//.test(port || "")) fail("usage: node scripts/flash_gateway.mjs COMx   (read the MAC first: the gateway ends in ...C4:10:28)");
// --output-dir: the core's post-build hook may exit 1 AFTER the image was written (a PowerShell Copy-Item on this PC), so judge by the file, not the exit code
const out = join(ROOT, "firmware", "gateway", "build", "app");
mkdirSync(out, { recursive: true });
const bin = join(out, "gateway.ino.bin");
const t0 = Date.now();
spawnSync(CLI, ["compile", "--fqbn", "esp32:esp32:esp32", "--build-property", "upload.maximum_size=2031616", "--libraries", join(ROOT, "libraries"), "--output-dir", out, join(ROOT, "firmware", "gateway")], { env: cliEnv, stdio: "inherit" });
if (!existsSync(bin) || statSync(bin).mtimeMs < t0 - 2000) fail("the compile did not produce a fresh " + bin);
console.log("flashing " + bin + " (" + statSync(bin).size + " bytes) to 0x10000 on " + port);
const r = spawnSync("py", ["-m", "esptool", "--port", port, "--baud", "460800", "write_flash", "0x10000", bin], { stdio: "inherit" });
if (r.status !== 0) fail("esptool failed");
console.log("\nApp image written at 0x10000 (nothing else touched).  The board was reset by esptool.");
