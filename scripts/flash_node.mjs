// Flash ONE sensor node (flow node = ESP32-C3, rain node = ESP32) with ONE command:   node scripts/flash_node.mjs COM37
//
// What it does, in order:
//   1. reads the chip type and MAC from the board on that port (esptool, nothing is written)
//   2. REFUSES the gateway (its MAC ends in C4:10:28; add more with  AQUA_NEVER_FLASH=AA:BB:..,CC:DD:..) - the gateway must only be flashed with flash_gateway.mjs
//   3. picks the sketch: ESP32-C3 -> firmware/flow_node_c3, ESP32 -> firmware/rain_node (override with  --sketch flow_node_c3|rain_node)
//   4. compiles it with the pinned toolchain and writes ONLY the application image at 0x10000
//      (bootloader, partition table, otadata and NVS stay: the node keeps its pairing code, calibration and "paired" flag)
//   5. prints the MAC again so you can note which physical node it was
// Close every serial monitor first (one program per port).  The board is reset by esptool when it is done.
import { existsSync, mkdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { ROOT, fail } from "./lib.mjs";
import { CLI, cliEnv } from "./setup_firmware_toolchain.mjs";

const args = process.argv.slice(2);
const port = args.find((a) => /^COM\d+$|^\/dev\//.test(a));
if (!port) fail("usage: node scripts/flash_node.mjs COMx [--sketch flow_node_c3|rain_node]");
const sk = args.indexOf("--sketch");
const forced = sk >= 0 ? args[sk + 1] : null;

const NEVER = ["C4:10:28", ...(process.env.AQUA_NEVER_FLASH || "").split(",").map((s) => s.trim().toUpperCase()).filter(Boolean)];
const SKETCHES = {
  flow_node_c3: { fqbn: "esp32:esp32:esp32c3:CDCOnBoot=cdc", label: "flow node (ESP32-C3)" },
  rain_node: { fqbn: "esp32:esp32:esp32", label: "rain node (ESP32)" },
};

const info = spawnSync("py", ["-m", "esptool", "--port", port, "--baud", "115200", "read-mac"], { encoding: "utf8" });
const text = (info.stdout || "") + (info.stderr || "");
if (info.status !== 0) fail("could not talk to the board on " + port + ":\n" + text.split("\n").slice(-8).join("\n"));
const chip = (/Chip (?:is|type:)\s*([^\n(]+)/.exec(text) || [])[1]?.trim() || "?";
const mac = ((/MAC:\s+([0-9a-f:]{17})/i.exec(text) || [])[1] || "").toUpperCase();
if (!mac) fail("could not read the MAC address of the board on " + port);
console.log(`board on ${port}: ${chip}, MAC ${mac}`);

if (NEVER.some((n) => mac === n.toUpperCase() || mac.endsWith(n.toUpperCase()))) {
  fail(`REFUSED: ${mac} is the GATEWAY (or a protected board). Flash it only with:  node scripts/flash_gateway.mjs ${port}`);
}
const name = forced || (/C3/i.test(chip) ? "flow_node_c3" : /^ESP32(-D0WD.*)?$/i.test(chip) || /ESP32 /.test(chip) || chip.startsWith("ESP32-D0") ? "rain_node" : null);
if (!name || !SKETCHES[name]) fail(`do not know which sketch fits chip "${chip}"; pass  --sketch flow_node_c3  or  --sketch rain_node`);
if (name === "flow_node_c3" && !/C3/i.test(chip)) fail(`the flow node sketch is for the ESP32-C3, this board is ${chip}`);
if (name === "rain_node" && /C3/i.test(chip)) fail(`the rain node sketch is for the classic ESP32, this board is ${chip}`);
console.log(`-> ${SKETCHES[name].label}: firmware/${name}`);

const out = join(ROOT, "firmware", name, "build", "app");
mkdirSync(out, { recursive: true });
const bin = join(out, name + ".ino.bin");
const t0 = Date.now();
// the core's post-build hook may exit 1 AFTER the image exists on some PCs, so judge by a fresh file
spawnSync(CLI, ["compile", "--fqbn", SKETCHES[name].fqbn, "--libraries", join(ROOT, "libraries"), "--output-dir", out, join(ROOT, "firmware", name)], { env: cliEnv, stdio: "inherit" });
if (!existsSync(bin) || statSync(bin).mtimeMs < t0 - 2000) fail("the compile did not produce a fresh " + bin);

console.log(`flashing ${bin} (${statSync(bin).size} bytes) to 0x10000 on ${port}`);
const r = spawnSync("py", ["-m", "esptool", "--port", port, "--baud", "460800", "write-flash", "0x10000", bin], { stdio: "inherit" });
if (r.status !== 0) fail("esptool failed (hold BOOT while plugging in the USB cable if the board does not answer)");
console.log(`\nDone: ${SKETCHES[name].label} ${mac} runs the new firmware (pairing, calibration and NVS untouched).`);
console.log("Watch it join:  python tools/serial_capture.py node=" + port + " --seconds 40   (expect  'PAIRED ch=6'  within ~30 s of a running gateway)");
