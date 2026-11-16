// Gateway portal UI regression test (jsdom) against a mock that mimics the real gateway API (gw_api.h):
//   * DELETE/PATCH /api/nodes/{n} take `type` from the QUERY string and default to "flow" when it is missing.
//   * removing a node frees its registry entry, the node reappears under /api/nearby.
// Needs the `jsdom` package: it is looked up in ./node_modules, in $AQUA_JSDOM_DIR, then in %TEMP%/uitest. If it is not found the
// test is reported as SKIPPED (exit 0) - say so when reporting results.
import { createRequire } from "node:module";
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { ROOT } from "./lib.mjs";

let JSDOM = null;
for (const base of [ROOT, process.env.AQUA_JSDOM_DIR, join(tmpdir(), "..", "..", "uitest"), "C:/Users/ROG/AppData/Local/Temp/uitest"].filter(Boolean)) {
  try { if (existsSync(join(base, "package.json"))) { JSDOM = createRequire(join(base, "package.json"))("jsdom").JSDOM; break; } } catch { /* next */ }
}
if (!JSDOM) { console.log("portal UI test SKIPPED: jsdom not installed (npm i jsdom in a scratch dir and set AQUA_JSDOM_DIR)"); process.exit(0); }

const P = join(ROOT, "firmware/gateway/portal");
const html = readFileSync(process.env.AQUA_PORTAL_SRC || join(P, "index.src.html"), "utf8").replace("/*@@QR@@*/", () => readFileSync(join(P, "qr.js"), "utf8"));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0, passes = 0;
const check = (c, m) => { if (c) passes++; else { fails++; console.log("FAIL", m); } };

function makeGateway() {
  const g = { log: [], nodes: [
    { number: 1, type: "flow", name: "Kitchen", mac: "44:7B:30:85:4B:88", state: "paired", online: true, batteryMv: 7400, rssi: -50, pplX10: 4500, txReduce: false },
    { number: 1, type: "rain", name: "Roof", mac: "AA:BB:CC:00:00:01", state: "paired", online: true, batteryMv: null, rssi: -60, pplX10: 0, txReduce: false }], nearby: [] };
  g.fetch = (url, o = {}) => {
    const m = (o.method || "GET").toUpperCase(), u = new URL(url, "http://192.168.1.50"), path = u.pathname;
    g.log.push(m + " " + path + u.search);
    const R = (code, j) => Promise.resolve({ ok: code < 300, status: code, json: () => Promise.resolve(j) });
    if (path === "/api/hello") return R(200, { gateway: "gw-TEST01", mode: "run", setupDone: true, adminSet: true, fw: "2.0" });
    if (path === "/api/login") return R(200, { token: "tok" });
    if ((o.headers || {})["X-Auth"] !== "tok") return R(401, { error: "auth" });
    if (path === "/api/status") return R(200, { gateway: "gw-TEST01", fw: "2.0", mode: "run", setupDone: true, uptime: 5, freeHeap: 1, time: { synced: true, epoch: 1 },
      wifi: { phase: "connected", error: "", ssid: "Home", ip: "192.168.1.50", rssi: -55, channel: 9, apActive: false }, mqtt: { phase: "connected", error: "", host: "b", port: 1883, tls: false, tlsVerified: false, remoteAdd: false },
      queue: { depth: 0, overflow: 0, rxDropped: 0 }, nodes: { registered: g.nodes.length, paired: g.nodes.length, nearby: 0 } });
    if (path === "/api/nodes" && m === "GET") return R(200, { nodes: g.nodes });
    if (path === "/api/nearby") return R(200, { nearby: g.nearby });
    const mm = path.match(/^\/api\/nodes\/(\d+)$/);
    if (mm && m === "DELETE") {
      const type = u.searchParams.get("type") === "rain" ? "rain" : "flow";          // = typeParam() in gw_api.h
      const i = g.nodes.findIndex((n) => n.type === type && n.number === +mm[1]);
      if (i < 0) return R(404, { error: "not_found" });
      const [n] = g.nodes.splice(i, 1); g.nearby.push({ mac: n.mac, type: n.type, rssi: -50, fw: 512, ageS: 1 });
      return R(200, { ok: true });
    }
    return R(404, { error: "nf" });
  };
  return g;
}

async function open(confirmAnswer) {                                                  // confirmAnswer=false mimics a mini browser that blocks window.confirm()
  const g = makeGateway();
  const dom = new JSDOM(html, { runScripts: "dangerously", url: "http://192.168.1.50/", pretendToBeVisual: true,
    beforeParse(w) { w.fetch = g.fetch; w.confirm = () => confirmAnswer; w.alert = () => {}; w.sessionStorage.setItem("aqtok", "tok"); } });
  await sleep(400);
  return { g, w: dom.window, d: dom.window.document };
}
const click = (c, s) => { const e = c.d.querySelector(s); if (!e) throw new Error("missing " + s); e.click(); };
const editBtn = (c, type) => [...c.d.querySelectorAll("[data-act=edit]")].find((b) => b.dataset.t === type);

for (const blocked of [false, true]) {
  const tag = blocked ? "(window.confirm blocked)" : "(window.confirm available)";
  // remove the flow node
  let c = await open(!blocked);
  check(!!editBtn(c, "flow"), `${tag} node list has an Edit button`);
  editBtn(c, "flow").click(); await sleep(50);
  click(c, "#del"); await sleep(30);
  check(!c.g.log.some((l) => l.startsWith("DELETE")), `${tag} Remove asks first (no request yet)`);
  check(!!c.d.querySelector("[data-yes]"), `${tag} an in-page confirmation is shown`);
  c.d.querySelector("[data-no]").click(); await sleep(20);
  check(!c.g.log.some((l) => l.startsWith("DELETE")) && !c.d.querySelector("[data-yes]"), `${tag} Cancel sends nothing`);
  click(c, "#del"); await sleep(30); c.d.querySelector("[data-yes]").click(); await sleep(500);
  check(c.g.log.includes("DELETE /api/nodes/1?type=flow"), `${tag} Yes sends DELETE /api/nodes/1?type=flow (got ${c.g.log.filter((l) => l.startsWith("DELETE"))})`);
  check(!c.g.nodes.some((n) => n.type === "flow") && c.g.nodes.some((n) => n.type === "rain"), `${tag} only the flow node was removed`);
  check(!editBtn(c, "flow") && !!editBtn(c, "rain"), `${tag} list refreshed after removal`);
  // remove the rain node that shares number 1 with the flow node: the `type` parameter must travel
  c = await open(!blocked);
  editBtn(c, "rain").click(); await sleep(50); click(c, "#del"); await sleep(30); c.d.querySelector("[data-yes]").click(); await sleep(500);
  check(c.g.log.includes("DELETE /api/nodes/1?type=rain"), `${tag} rain node: DELETE carries type=rain`);
  check(c.g.nodes.length === 1 && c.g.nodes[0].type === "flow", `${tag} rain removed, flow node untouched`);
}
// the Status view shows the address card and factory reset also uses the in-page confirmation
{
  const c = await open(false);
  c.d.querySelector('[data-nav="status"]').click(); await sleep(50);
  check(/http:\/\/192\.168\.1\.50/.test(c.d.body.textContent) && /aquaguard\.local/.test(c.d.body.textContent), "run view shows the gateway address");
  click(c, "#freset"); await sleep(30);
  check(!!c.d.querySelector("[data-yes]") && !c.g.log.some((l) => l.includes("factory-reset")), "factory reset asks in the page and sends nothing yet");
}
console.log(`${passes} portal UI checks passed, ${fails} failed`);
process.exit(fails ? 1 : 0);
