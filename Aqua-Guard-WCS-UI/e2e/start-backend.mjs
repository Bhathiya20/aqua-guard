// Starts a throw-away backend for the e2e tests: fresh database seeded with a gateway and flow nodes 2-5 (simulator), MQTT off, port 5190.
import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const backend = resolve(here, "..", "backend");
const py = join(backend, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const dir = join(here, ".tmp"); mkdirSync(dir, { recursive: true });
const db = join(dir, "e2e.db");
for (const s of ["", "-wal", "-shm"]) rmSync(db + s, { force: true });
const env = { ...process.env, AQUAGUARD_DB_PATH: db, AQUAGUARD_MQTT_ENABLED: "false", AQUAGUARD_HOST: "127.0.0.1", AQUAGUARD_PORT: "5190",
  AQUAGUARD_ADMIN_EMAIL: "e2e@aquaguard.local", AQUAGUARD_ADMIN_PASSWORD: "E2e.Aqua1x", AQUAGUARD_CORS_ORIGINS: "http://127.0.0.1:5290", AQUAGUARD_CMD_KEY: "", PYTHONUNBUFFERED: "1" };
const start = new Date(Date.now() - 3 * 3600e3).toISOString().slice(0, 16);
const r = spawnSync(py, ["simulate_sensors.py", "--pipeline", "../examples/four-node-house.json", "--seconds", "60", "--start", start], { cwd: backend, env, encoding: "utf8" });
if (r.status !== 0) { console.error(r.stdout, r.stderr); process.exit(1); }
const api = spawn(py, ["app.py"], { cwd: backend, env, stdio: "inherit" });
const stop = () => { api.kill(); process.exit(0); };
process.on("SIGINT", stop); process.on("SIGTERM", stop);
api.on("exit", (c) => process.exit(c ?? 0));
