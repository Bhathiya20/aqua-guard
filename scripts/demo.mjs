// npm run demo - a self-contained leak-detection demo that never touches the real database or the real app.
//   * fresh database  .demo/demo.db   (rebuilt on every start; the real DB is never opened)
//   * API on 127.0.0.1:5100, web app on http://127.0.0.1:5273   (the real app uses localhost:5000 / :5173; a different host name also keeps the login cookies apart)
//   * MQTT is OFF, so the demo can neither read nor publish anything on the real broker or to the real gateway
//   * imports examples/four-node-house.json, then simulates a 1.2 L/min leak between Node 2 and the branches
// Ctrl+C stops everything.  Options: --leak-at p2 | p1 | p3 | p4   --lpm 1.2   --no-open
import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { APP, BACKEND, ROOT, VENV_PY, fail, requireVenv } from "./lib.mjs";

requireVenv();
const arg = (n, d) => { const i = process.argv.indexOf(n); return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : d; };
const API_PORT = 5100, WEB_PORT = 5273, HOST = "127.0.0.1";
const API = `http://${HOST}:${API_PORT}/api`, WEB = `http://${HOST}:${WEB_PORT}`;
const EMAIL = "demo@aquaguard.local", PASSWORD = "Demo.Aqua1";
const leakAt = arg("--leak-at", "p2"), lpm = arg("--lpm", "1.2");
const dir = join(ROOT, ".demo"), db = join(dir, "demo.db");
mkdirSync(dir, { recursive: true });
for (const f of ["", "-wal", "-shm"]) rmSync(db + f, { force: true });

const env = {
  ...process.env, AQUAGUARD_DB_PATH: db, AQUAGUARD_MQTT_ENABLED: "false", AQUAGUARD_HOST: HOST, AQUAGUARD_PORT: String(API_PORT),
  AQUAGUARD_ADMIN_EMAIL: EMAIL, AQUAGUARD_ADMIN_PASSWORD: PASSWORD, AQUAGUARD_CORS_ORIGINS: `${WEB},http://localhost:${WEB_PORT}`,
  AQUAGUARD_CMD_KEY: "", AQUAGUARD_MQTT_USER: "", AQUAGUARD_MQTT_PASSWORD: "", PYTHONUNBUFFERED: "1",
};
const sim = (start, seconds, extra = []) => {
  const r = spawnSync(VENV_PY, ["simulate_sensors.py", "--pipeline", "../examples/four-node-house.json", "--seconds", String(seconds), "--start", start, ...extra], { cwd: BACKEND, env, encoding: "utf8" });
  if (r.status !== 0) fail(`simulator failed:\n${r.stdout}\n${r.stderr}`);
  return r.stdout;
};
const iso = (minAgo) => new Date(Date.now() - minAgo * 60000).toISOString().slice(0, 16);

const children = [];
const stop = () => { for (const c of children) { try { c.kill(); } catch { /* already gone */ } } process.exit(0); };
process.on("SIGINT", stop); process.on("SIGTERM", stop);

console.log("[demo] 1/5 creating the demo gateway + nodes 2-5 in .demo/demo.db");
sim(iso(180), 60);

console.log(`[demo] 2/5 starting the demo API on ${API}`);
const api = spawn(VENV_PY, ["app.py"], { cwd: BACKEND, env, stdio: ["ignore", "pipe", "pipe"] });
children.push(api);
api.stdout.on("data", () => {}); api.stderr.on("data", () => {});
api.on("exit", (c) => { if (c) fail(`demo API exited (${c})`); });

const wait = async (url, what) => { for (let i = 0; i < 60; i++) { try { if ((await fetch(url)).ok) return; } catch { /* not up yet */ } await new Promise((r) => setTimeout(r, 500)); } fail(`${what} did not start`); };
await wait(`${API}/health`, "demo API");

console.log("[demo] 3/5 importing examples/four-node-house.json");
const login = await fetch(`${API}/login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: EMAIL, password: PASSWORD }) });
if (!login.ok) fail(`demo login failed (${login.status})`);
const csrf = (await login.json()).csrfToken, cookie = (login.headers.getSetCookie?.() ?? []).map((c) => c.split(";")[0]).join("; ");
const doc = JSON.parse(readFileSync(join(APP, "examples", "four-node-house.json"), "utf8"));
const imp = await fetch(`${API}/pipelines`, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf, Cookie: cookie }, body: JSON.stringify({ name: "Four-node house (demo)", doc }) });
const imported = await imp.json();
if (!imp.ok || !imported.valid) fail(`import failed: ${JSON.stringify(imported)}`);

console.log(`[demo] 4/5 simulating a ${lpm} L/min leak at ${leakAt} (about 48 min of virtual time ending now)`);
const out = sim(iso(49), 2900, ["--leak-at", leakAt, "--leak-lpm", lpm]);
console.log(out.split("\n").filter((l) => /ANALYSIS|After|Main supply/.test(l)).join("\n"));

console.log(`[demo] 5/5 starting the web app on ${WEB}`);
const npm = process.platform === "win32" ? "npm.cmd" : "npm";
const web = spawn(npm, ["run", "dev", "--", "--host", HOST, "--port", String(WEB_PORT), "--strictPort"], { cwd: APP, env: { ...env, VITE_API_BASE: API }, stdio: "inherit", shell: process.platform === "win32" });
children.push(web);
web.on("exit", (c) => { if (c) fail(`demo web app exited (${c})`); });
await wait(WEB, "demo web app");

console.log(`\n  DEMO READY  ->  ${WEB}\n  login: ${EMAIL}  /  ${PASSWORD}\n  Open Leak Detection, Alerts, or Pipeline -> Four-node house -> Live.\n  The data is historic: after ~30 s the nodes show "unknown" and the gateway "offline" (expected).\n  Real database and real app (localhost:5000 / :5173) are untouched.  Ctrl+C to stop.\n`);
if (!process.argv.includes("--no-open")) {
  if (process.platform === "win32") spawn("cmd", ["/c", "start", "", WEB], { stdio: "ignore", detached: true }).unref();
  else spawn(process.platform === "darwin" ? "open" : "xdg-open", [WEB], { stdio: "ignore", detached: true }).unref();
}
