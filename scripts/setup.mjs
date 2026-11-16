// First-time setup: Python venv + backend deps, frontend deps, backend/.env from the template.
import { copyFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { APP, BACKEND, VENV_PY, fail, systemPython } from "./lib.mjs";

const run = (cmd, args, cwd) => {
  console.log(`\n> ${cmd} ${args.join(" ")}`);
  const r = spawnSync(cmd, args, { cwd, stdio: "inherit", shell: process.platform === "win32" && cmd === "npm" });
  if (r.status !== 0) fail(`Command failed: ${cmd} ${args.join(" ")}`);
};

if (!existsSync(VENV_PY)) {
  const py = systemPython();
  if (!py) fail("Python 3 not found. Install Python 3.10+ and re-run.");
  run(py[0], [...py[1], "-m", "venv", ".venv"], BACKEND);
}
run(VENV_PY, ["-m", "pip", "install", "--upgrade", "pip"], BACKEND);
run(VENV_PY, ["-m", "pip", "install", "-r", "requirements.txt"], BACKEND);
run(VENV_PY, ["-m", "pip", "install", "-r", join("..", "..", "tools", "requirements.txt")], BACKEND);   // tools tests (QR / labels) need qrcode, pillow ...
if (!existsSync(join(APP, "node_modules"))) run("npm", ["install"], APP);
if (!existsSync(join(BACKEND, ".env"))) {
  copyFileSync(join(BACKEND, ".env.example"), join(BACKEND, ".env"));
  console.log("\n[aqua-guard] Created backend/.env from the template - review MQTT and CORS settings.");
}
console.log("\n[aqua-guard] Setup complete. Start everything with:  npm run dev");
