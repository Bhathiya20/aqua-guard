// Starts the Flask API. The MQTT subscriber runs INSIDE this single process (one subscriber only).
// Debug/reloader is off on purpose: the reloader would start a second process and a second MQTT client.
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { BACKEND, VENV_PY, fail, portFree, requireVenv } from "./lib.mjs";

requireVenv();
const args = process.argv.slice(2);
const si = args.indexOf("--script");
const script = si >= 0 ? args[si + 1] : "app.py";
const extra = si >= 0 ? args.slice(si + 2) : [];

if (script === "app.py") {
  const port = Number(process.env.AQUAGUARD_PORT || 5000);
  if (!(await portFree(port))) {
    fail(`Port ${port} is already in use (another backend still running?). Stop it or set AQUAGUARD_PORT.`);
  }
  if (!existsSync(join(BACKEND, ".env"))) {
    console.log("[aqua-guard] No backend/.env found - using built-in defaults (public dev MQTT broker). Copy .env.example to .env to configure.");
  }
}

const child = spawn(VENV_PY, ["-u", script, ...extra], { cwd: BACKEND, stdio: "inherit", env: { ...process.env, PYTHONUNBUFFERED: "1" } });
const stop = () => { if (!child.killed) child.kill(); };
process.on("SIGINT", stop); process.on("SIGTERM", stop); process.on("exit", stop);
child.on("exit", (code) => process.exit(code ?? 0));
