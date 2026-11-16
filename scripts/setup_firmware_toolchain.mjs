// Reproducible firmware toolchain: arduino-cli + ESP32 core + libraries, all pinned, installed into ./.toolchain
// (git-ignored, isolated from any Arduino IDE install).   Usage:  node scripts/setup_firmware_toolchain.mjs
// Then compile with:  node scripts/compile_firmware.mjs <gateway|flow_node_c3|rain_node|all>
import { existsSync, mkdirSync, rmSync } from "node:fs";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { ROOT, fail } from "./lib.mjs";

export const CLI_VERSION = "1.6.0";
export const ESP32_CORE = "3.3.5";
export const ESP32_INDEX = "https://espressif.github.io/arduino-esp32/package_esp32_index.json";
export const LIBS = ["PubSubClient@2.8.0", "ESP Async WebServer@3.12.1", "Async TCP@3.6.1", "U8g2@2.36.19"];

export const TC = join(ROOT, ".toolchain");
const win = process.platform === "win32";
export const CLI = join(TC, "bin", win ? "arduino-cli.exe" : "arduino-cli");
export const cliEnv = {
  ...process.env,
  ARDUINO_DIRECTORIES_DATA: join(TC, "data"),
  ARDUINO_DIRECTORIES_USER: join(TC, "user"),
  ARDUINO_DIRECTORIES_DOWNLOADS: join(TC, "dl"),
  ARDUINO_BOARD_MANAGER_ADDITIONAL_URLS: ESP32_INDEX,
};

export function cli(args, opts = {}) {
  const r = spawnSync(CLI, args, { env: cliEnv, stdio: opts.capture ? "pipe" : "inherit", encoding: "utf8" });
  if (r.status !== 0 && !opts.allowFail) fail(`arduino-cli ${args.join(" ")} failed (exit ${r.status})\n${r.stderr || ""}`);
  return r;
}

function download() {
  const os = process.platform === "win32" ? "Windows_64bit.zip" : process.platform === "darwin" ? "macOS_64bit.tar.gz" : "Linux_64bit.tar.gz";
  const url = `https://github.com/arduino/arduino-cli/releases/download/v${CLI_VERSION}/arduino-cli_${CLI_VERSION}_${os}`;
  const dl = join(TC, os);
  mkdirSync(join(TC, "bin"), { recursive: true });
  console.log(`[toolchain] downloading ${url}`);
  let r = spawnSync("curl", ["-fL", "-o", dl, url], { stdio: "inherit" });
  if (r.status !== 0) fail("download failed (need curl and internet)");
  // `tar` (bsdtar) ships with Windows 10+, macOS and Linux and also unpacks .zip
  r = spawnSync("tar", ["-xf", dl, "-C", join(TC, "bin")], { stdio: "inherit" });
  if (r.status !== 0) fail("could not unpack arduino-cli");
  rmSync(dl);
}

if (import.meta.url === `file:///${process.argv[1].replace(/\\/g, "/")}` || process.argv[1]?.endsWith("setup_firmware_toolchain.mjs")) {
  const have = existsSync(CLI) && (cli(["version"], { capture: true, allowFail: true }).stdout || "").includes(CLI_VERSION);
  if (!have) download();
  cli(["version"]);
  cli(["core", "update-index"]);
  cli(["core", "install", `esp32:esp32@${ESP32_CORE}`]);
  cli(["lib", "update-index"]);
  for (const l of LIBS) cli(["lib", "install", l]);
  console.log("\n[toolchain] ready. Installed:");
  cli(["core", "list"]);
  cli(["lib", "list"]);
}
