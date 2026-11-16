// Shared helpers for the launcher scripts (cross-platform: Windows PowerShell, macOS, Linux).
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import net from "node:net";

export const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
export const APP = join(ROOT, "Aqua-Guard-WCS-UI");
export const BACKEND = join(APP, "backend");
const win = process.platform === "win32";
export const VENV_PY = join(BACKEND, ".venv", win ? "Scripts" : "bin", win ? "python.exe" : "python");

export function fail(msg) {
  console.error(`\n[aqua-guard] ERROR: ${msg}\n`);
  process.exit(1);
}

export function systemPython() {
  for (const cmd of win ? [["py", ["-3"]], ["python", []]] : [["python3", []], ["python", []]]) {
    const r = spawnSync(cmd[0], [...cmd[1], "--version"], { encoding: "utf8" });
    if (r.status === 0) return cmd;
  }
  return null;
}

export function requireVenv() {
  if (!existsSync(VENV_PY)) fail("Python environment not found. Run:  npm run setup");
}

export function portFree(port, host = "127.0.0.1") {
  return new Promise((res) => {
    const s = net.createServer().once("error", () => res(false)).once("listening", () => s.close(() => res(true)));
    s.listen(port, host);
  });
}
