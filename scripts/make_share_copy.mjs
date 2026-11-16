// node scripts/make_share_copy.mjs  ->  Aqua-Guard-Share-<date>.zip next to the project folder, WITHOUT any secret.
// Leaves out: aqua_secrets.h, .env files, database files, nodes/*/secret.txt, stickers, logs/, NVS backups, node_modules, build folders, .toolchain, venvs, .git.
// Then it opens the zip again and scans every file: (1) for the REAL secret values of this machine (read from the local secret files), (2) for generic
// secret patterns outside examples/tests. If anything is found the zip is deleted and the script fails. It prints what was left out.
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync } from "node:fs";
import { join, relative, sep, dirname, basename } from "node:path";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import { ROOT, fail } from "./lib.mjs";

const SKIP_DIRS = new Set(["node_modules", "dist", "build", "__pycache__", ".venv", ".pytest_cache", "test-results", "playwright-report", ".tmp", ".toolchain", "logs", ".git", ".demo", "labels", "data"]);
const SKIP_NAME = (n) => n === "aqua_secrets.h" || n === ".env" || n === ".secret_key" || n === "secret.txt" || n.startsWith("sticker.") || /\.(db|db-wal|db-shm|sqlite|zip|pyc|log)$/i.test(n) || /^nvs/i.test(n) || /nvs.*\.(bin|img)$/i.test(n);
const KEEP_EXAMPLES = new Set([".env.example", "aqua_secrets.example.h"]);

const SECRET_PATTERNS = [
  [/(?:0x[0-9a-f]{2},\s*){15,}0x[0-9a-f]{2}/i, "16+ byte key array"],
  [/(?:PSK|KEY|SECRET|TOKEN)\w*\s*=\s*["']?[0-9a-f]{32,}/i, "hex key assignment"],
  [/(?:wifi|ssid|password|passwd|pass)\w*\s*[=:]\s*["'][^"'<>{}$\s][^"']{5,}["']/i, "credential assignment"],
  [/pairing\s*code\s*[:=]\s*[A-Z0-9-]{6,}/i, "pairing code"],
  [/\b[0-9A-F]{2}(?::[0-9A-F]{2}){5}\b[^\n]{0,60}\b[A-Z0-9]{4}-[A-Z0-9]{4}\b/, "MAC with pairing code"],
];
// Generic patterns are only applied OUTSIDE these paths (examples, test fixtures, UI labels and log format strings use fake or placeholder values).
const PATTERN_EXEMPT = /(\.example\.|[\\/](tests?|e2e|fixtures?)[\\/]|^tests?[\\/]|\.test\.|\.spec\.|test_|make_node_labels\.py|scripts[\\/]demo\.mjs|index\.src\.html|gateway\.ino|docs[\\/])/i;
const SAFE_LINE = /example|placeholder|changeme|your[_ -]|<.*>|process\.env|os\.environ|getenv|\$\{/i;
const NEWLINE = new RegExp("\\r?\\n");

// Known values: the REAL secrets of this machine (if the secret files are present here). Any file in the zip containing one fails the scan, whatever its path.
// Public defaults such as the MQTT topic prefix are not secrets and are skipped.
const knownSecrets = () => {
  const t = new Set();
  const add = (v) => {
    v = String(v).trim().replace(/^["']|["']$/g, "");
    if (v.length >= 8 && !/^\d+$/.test(v) && !/^(localhost|127\.0\.0\.1|aquaguard|true|false)$/i.test(v) && !/^waterleak\//.test(v)) t.add(v);
  };
  const rd = (f) => { try { return readFileSync(join(ROOT, f), "utf8"); } catch { return ""; } };
  const h = rd("libraries/AquaProtocol/src/aqua_secrets.h");
  for (const m of h.matchAll(/(?:0x[0-9a-f]{2}\s*,\s*){7,}0x[0-9a-f]{2}/gi)) t.add(m[0].replace(/\s+/g, ""));
  for (const m of h.matchAll(/"([^"\n]{6,})"/g)) add(m[1]);
  for (const l of rd("Aqua-Guard-WCS-UI/backend/.env").split(NEWLINE)) if (l.includes("=") && !l.trim().startsWith("#")) add(l.split("=").slice(1).join("="));
  const nd = join(ROOT, "nodes");
  if (existsSync(nd)) for (const n of readdirSync(nd)) {
    const f = join(nd, n, "secret.txt");
    if (existsSync(f)) for (const w of readFileSync(f, "utf8").matchAll(/[A-Za-z0-9+\/=_-]{8,}/g)) add(w[0]);
  }
  return [...t];
};
const KNOWN = knownSecrets();

const date = new Date().toISOString().slice(0, 10);
const out = join(dirname(ROOT), `Aqua-Guard-Share-${date}.zip`);
const stage = join(tmpdir(), `aqua-share-${process.pid}`, basename(ROOT));
rmSync(dirname(stage), { recursive: true, force: true });
mkdirSync(stage, { recursive: true });
const left = [];
const walk = (dir) => {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name), rel = relative(ROOT, p);
    if (e.isDirectory()) { if (SKIP_DIRS.has(e.name)) { left.push(rel + sep); continue; } walk(p); continue; }
    if (SKIP_NAME(e.name) && !KEEP_EXAMPLES.has(e.name)) { left.push(rel); continue; }
    const d = join(stage, rel); mkdirSync(dirname(d), { recursive: true }); cpSync(p, d);
  }
};
walk(ROOT);

// zip (PowerShell on Windows, zip elsewhere)
rmSync(out, { force: true });
const z = process.platform === "win32"
  ? spawnSync("powershell", ["-NoProfile", "-Command", `Compress-Archive -Path '${stage}' -DestinationPath '${out}' -Force`], { stdio: "inherit" })
  : spawnSync("zip", ["-qr", out, basename(ROOT)], { cwd: dirname(stage), stdio: "inherit" });
if (z.status !== 0) fail("Could not create the zip.");

// scan what is IN the zip: extract it again and look at every text file
const chk = join(tmpdir(), `aqua-share-check-${process.pid}`);
rmSync(chk, { recursive: true, force: true }); mkdirSync(chk, { recursive: true });
const u = process.platform === "win32"
  ? spawnSync("powershell", ["-NoProfile", "-Command", `Expand-Archive -Path '${out}' -DestinationPath '${chk}' -Force`], { stdio: "inherit" })
  : spawnSync("unzip", ["-q", out, "-d", chk], { stdio: "inherit" });
if (u.status !== 0) { rmSync(out, { force: true }); fail("Could not re-open the zip to scan it."); }
const hits = [];
const scan = (dir) => {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name), rel = relative(chk, p);
    if (e.isDirectory()) { scan(p); continue; }
    if (/^(aqua_secrets\.h|\.env|secret\.txt)$/.test(e.name) || /\.db$/i.test(e.name) || e.name.startsWith("sticker.")) { hits.push(`${rel}: forbidden file`); continue; }
    if (statSync(p).size > 2_000_000 || /\.(png|jpg|pdf|ico|woff2?|gz|bin)$/i.test(e.name)) continue;
    const text = readFileSync(p, "utf8"), squeezed = text.replace(/\s+/g, "");
    if (KNOWN.some((k) => text.includes(k) || squeezed.includes(k))) { hits.push(`${rel}: contains a real secret value of this machine`); continue; }
    if (PATTERN_EXEMPT.test(rel)) continue;
    text.split(NEWLINE).forEach((line, i) => {
      if (SAFE_LINE.test(line)) return;
      for (const [re, why] of SECRET_PATTERNS) if (re.test(line)) hits.push(`${rel}:${i + 1}: ${why}`);
    });
  }
};
scan(chk);
rmSync(dirname(stage), { recursive: true, force: true }); rmSync(chk, { recursive: true, force: true });
if (hits.length) {
  rmSync(out, { force: true });
  console.error("SECRET SCAN FAILED. The zip was deleted. Findings:\n  " + hits.slice(0, 40).join("\n  "));
  process.exit(1);
}
console.log(`Secret scan: clean (checked against ${KNOWN.length} real secret values from this machine plus generic patterns).`);
console.log(`Share copy written: ${out} (${(statSync(out).size / 1048576).toFixed(1)} MB).`);
console.log("Left out of the zip:\n  " + left.sort().join("\n  "));
