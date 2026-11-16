// Runs tests/qr_vectors.json against the gateway portal's JS parser (firmware/gateway/portal/qr.js).
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { ROOT } from "./lib.mjs";

const AquaQr = createRequire(import.meta.url)(join(ROOT, "firmware/gateway/portal/qr.js"));
const { cases } = JSON.parse(readFileSync(join(ROOT, "tests/qr_vectors.json"), "utf8"));
let pass = 0, fail = 0;
for (const c of cases) {
  let got, err;
  try { got = AquaQr.parse(c.in); } catch (e) { err = e.kind; }
  const ok = c.ok ? (got && got.mac === c.ok.mac && got.code === c.ok.code && got.type === c.ok.type) : err === c.err;
  if (ok) pass++; else { fail++; console.log("FAIL", JSON.stringify(c.in), "->", got || err); }
}
console.log(`${pass} portal QR checks passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
