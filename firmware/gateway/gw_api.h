/*
 * Gateway web server: captive portal, static portal files (gzip from LittleFS) and the JSON REST API.
 * Included by gateway.ino after the shared state is declared.  Handlers run in the async_tcp task: they take `mx`
 * for registry / core / config access and never touch Wi-Fi or ESP-NOW directly (they set flags the main loop acts on).
 *
 *   GET  /api/hello            public: mode, setup state, whether a password is needed
 *   POST /api/login            password -> token (header X-Auth); 5 wrong attempts lock logins for 60 s
 *   POST /api/admin            set the admin password (no auth only while none is set); returns a token
 *   GET  /api/status           live Wi-Fi / MQTT / time / node counts (never returns a password)
 *   GET  /api/wifi/scan        {"scanning":true} until the scan finished, then {"networks":[...]}
 *   POST /api/wifi             ssid, pass -> starts the connection; progress in /api/status
 *   GET/POST /api/mqtt         broker settings (GET never returns the password)
 *   GET  /api/nodes            registry with live state;   GET /api/nearby   nodes waiting to pair
 *   POST /api/nodes            type, mac, code, name  (add)       POST /api/nodes/replace  type, number, mac, code
 *   PATCH  /api/nodes/{n}?type=flow|rain   name, pplX10, txReduce, txDbmX10 (85|110|130|150)     DELETE /api/nodes/{n}?type=...
 *   POST /api/setup/finish     leave SETUP mode          POST /api/setup/enter   re-enter SETUP (authenticated)
 *   POST /api/factory-reset    wipes config + registry
 * With an admin password set, every endpoint except /api/hello, /api/login, the page and the captive-portal URLs needs the token.
 * Without one, the API is open - which is only possible before setup, while the WPA2 setup AP is the access control.
 */
#pragma once
#include <ESPAsyncWebServer.h>

static AsyncWebServer server(80);
static volatile bool wifiReq = false, enterSetupReq = false, factoryResetReq = false;
static volatile uint32_t restartAt = 0;
static char tokens[4][33]; static uint32_t tokenExp[4];
static uint8_t loginFails = 0; static uint32_t lockUntil = 0;

static const char FALLBACK_HTML[] PROGMEM =
  "<!doctype html><meta name=viewport content='width=device-width'><title>Aqua Guard</title><body style='font-family:sans-serif;margin:2em'>"
  "<h2>Aqua Guard gateway</h2><p>The portal files are not on this gateway yet (LittleFS is empty).</p>"
  "<p>On the computer: <code>node scripts/build_portal.mjs --flash COMx</code>, then reload this page.</p>"
  "<p>The REST API is already running: <a href='/api/hello'>/api/hello</a></p>";

static void sendJson(AsyncWebServerRequest* r, int code, const String& body) {
  AsyncWebServerResponse* resp = r->beginResponse(code, "application/json", body);
  resp->addHeader("Cache-Control", "no-store"); r->send(resp);
}
static void sendErr(AsyncWebServerRequest* r, int code, const char* err) { sendJson(r, code, String("{\"error\":\"") + err + "\"}"); }
static String P(AsyncWebServerRequest* r, const char* n) {
  if (r->hasParam(n, true)) return r->getParam(n, true)->value();
  if (r->hasParam(n)) return r->getParam(n)->value();
  return String();
}
static bool hasP(AsyncWebServerRequest* r, const char* n) { return r->hasParam(n, true) || r->hasParam(n); }

static bool authOk(AsyncWebServerRequest* r) {
  if (!cfg.adminSet()) return true;
  if (!r->hasHeader("X-Auth")) return false;
  String t = r->header("X-Auth"); uint32_t now = millis();
  for (int i = 0; i < 4; i++) if (tokens[i][0] && (int32_t)(tokenExp[i] - now) > 0 && t == tokens[i]) { tokenExp[i] = now + 30UL * 60000UL; return true; }
  return false;
}
#define NEED_AUTH(r) do { if (!authOk(r)) { sendErr(r, 401, "auth"); return; } } while (0)
static String newToken() {
  static uint8_t next = 0; uint8_t b[16]; esp_fill_random(b, 16); static const char* H = "0123456789abcdef";
  char* t = tokens[next]; for (int i = 0; i < 16; i++) { t[2 * i] = H[b[i] >> 4]; t[2 * i + 1] = H[b[i] & 15]; } t[32] = 0;
  tokenExp[next] = millis() + 30UL * 60000UL; String s = t; next = (next + 1) % 4; return s;
}

static const char* wifiPhaseName(Phase p) { return p == PH_CONNECTING ? "connecting" : p == PH_CONNECTED ? "connected" : p == PH_FAILED ? "failed" : "idle"; }

static String statusJson() {
  uint32_t now = millis(); LOCK();
  uint8_t reg_n = reg.count(), nearby = core.nearbyCount(now), paired = 0;
  for (uint8_t s = 1; s <= AQUA_MAX_SLOTS; s++) if (reg.at(s).used && reg.at(s).state == 2) paired++;
  UNLOCK();
  String o = "{\"gateway\":\"" + String(gatewayId) + "\",\"fw\":\"" + String(AQUA_FW_VERSION >> 8) + "." + String(AQUA_FW_VERSION & 0xFF) + "\",\"mode\":\"" + (setupMode ? "setup" : "run") + "\"";
  o += ",\"setupDone\":"; o += cfg.setupDone ? "true" : "false"; o += ",\"uptime\":" + String(now / 1000) + ",\"freeHeap\":" + String(ESP.getFreeHeap());
  o += ",\"time\":{\"synced\":"; o += epochNow() ? "true" : "false"; o += ",\"epoch\":" + String(epochNow()) + "}";
  o += ",\"wifi\":{\"phase\":\"" + String(wifiPhaseName(wifiPhase)) + "\",\"error\":" + jq(wifiErr) + ",\"ssid\":" + jq(cfg.ssid) + ",\"ip\":\"" + (wifiUp() ? WiFi.localIP().toString() : String("")) +
       "\",\"rssi\":" + String(wifiUp() ? WiFi.RSSI() : 0) + ",\"channel\":" + String(currentChannel()) + ",\"apActive\":" + (apActive ? "true" : "false") + ",\"apLeftS\":" + String(tempApUntil ? (long)((int32_t)(tempApUntil - millis()) / 1000) : 0L) + ",\"mdns\":\"aquaguard.local\"}";
  o += ",\"mqtt\":{\"phase\":\"" + String(wifiPhaseName(mqttPhase)) + "\",\"error\":" + jq(mqttErr) + ",\"host\":" + jq(cfg.mqttHost) + ",\"port\":" + String(cfg.mqttPort) +
       ",\"tls\":" + (cfg.mqttTls ? "true" : "false") + ",\"tlsVerified\":" + ((cfg.mqttTls && !cfg.tlsInsecure) ? "true" : "false") + ",\"remoteAdd\":" + ((codeChannelOk() && epochNow()) ? "true" : "false") + "}";
  o += ",\"queue\":{\"depth\":" + String(qCount) + ",\"overflow\":" + String(qOverflow) + ",\"rxDropped\":" + String(rxDropped) + "}";
  o += ",\"nodes\":{\"registered\":" + String(reg_n) + ",\"paired\":" + String(paired) + ",\"nearby\":" + String(nearby) + "}}";
  return o;
}

static String nodesJson() {
  uint32_t now = millis(); LOCK(); String o = registryJson(now); UNLOCK(); return o;
}
static String nearbyJson() {
  uint32_t now = millis(); String o = "{\"nearby\":["; LOCK();
  bool first = true; AquaNearby nb;
  for (uint8_t i = 0; i < core.nearbyCount(now); i++) if (core.nearbyAt(i, now, &nb)) {
    char mac[18]; aquaMacFormat(nb.mac, mac);
    if (!first) o += ","; first = false;
    o += "{\"mac\":\"" + String(mac) + "\",\"type\":\"" + typeName(nb.devType) + "\",\"rssi\":" + String(nb.rssi) + ",\"fw\":" + String(nb.fwVersion) + ",\"ageS\":" + String((now - nb.lastSeenMs) / 1000) + "}";
  }
  UNLOCK(); return o + "]}";
}

static int regErrHttp(int rc, const char** msg) {
  switch (rc) {
    case AQUA_REG_DUP_MAC: *msg = "duplicate_mac"; return 409;
    case AQUA_REG_FULL: *msg = "registry_full"; return 507;
    case AQUA_REG_BAD_CODE: *msg = "bad_code"; return 400;
    case AQUA_REG_NOT_FOUND: *msg = "not_found"; return 404;
    default: *msg = "bad_argument"; return 400;
  }
}
static uint8_t typeParam(AsyncWebServerRequest* r) { String t = P(r, "type"); t.toLowerCase(); return t == "rain" ? AQUA_DEV_RAIN : AQUA_DEV_FLOW; }

static void handleNodeById(AsyncWebServerRequest* r) {                    // PATCH / DELETE /api/nodes/{n}
  NEED_AUTH(r);
  String u = r->url(); int number = u.substring(11).toInt(); uint8_t type = typeParam(r);
  if (number <= 0 || number > 250) { sendErr(r, 400, "bad_number"); return; }
  uint8_t rnd[8]; (void)rnd; int rc = 0; const char* msg = "";
  LOCK();
  if (r->method() == HTTP_DELETE) rc = reg.remove(type, (uint8_t)number);
  else {
    rc = reg.findByNumber(type, number); if (rc < 0) rc = AQUA_REG_NOT_FOUND;
    if (rc > 0 && hasP(r, "name")) rc = reg.rename(type, number, P(r, "name").c_str());
    if (rc > 0 && (hasP(r, "pplX10") || hasP(r, "txReduce") || hasP(r, "txDbmX10"))) {
      const AquaRegEntry& e = reg.at(rc);
      uint16_t ppl = hasP(r, "pplX10") ? (uint16_t)P(r, "pplX10").toInt() : e.pplX10;
      uint8_t fl = hasP(r, "txReduce") ? (P(r, "txReduce").toInt() ? aquaTxFlagsForDbmX10(85) : 0) : e.cfgFlags;
      if (hasP(r, "txDbmX10")) { fl = aquaTxFlagsForDbmX10((uint16_t)P(r, "txDbmX10").toInt()); if (fl == 0xFF) { UNLOCK(); sendErr(r, 400, "bad_tx_power"); return; } }
      rc = reg.setCalibration(type, number, ppl, fl);
    }
  }
  if (rc > 0) g_dirty = true;
  UNLOCK();
  if (rc <= 0) { int code = regErrHttp(rc, &msg); sendErr(r, code, msg); return; }
  sendJson(r, 200, "{\"ok\":true}");
}

static void redirectToPortal(AsyncWebServerRequest* r) { r->redirect("http://192.168.4.1/"); }
// true only for a request that arrived on the setup AP: a LAN client (STA ip / aquaguard.local) must never be bounced to 192.168.4.1
static bool viaAp(AsyncWebServerRequest* r) { return apActive && r->client()->localIP() == WiFi.softAPIP(); }

static void sendPortalFile(AsyncWebServerRequest* r, const char* path, const char* type) {
  if (LittleFS.exists(path)) {
    AsyncWebServerResponse* resp = r->beginResponse(LittleFS, path, type);
    resp->addHeader("Content-Encoding", "gzip"); resp->addHeader("Cache-Control", "no-cache"); r->send(resp);
  } else r->send(200, "text/html", FPSTR(FALLBACK_HTML));
}

static void gwApiBegin() {
  // count requests that reach the server (diagnostics: the [NET] line shows how many arrived and from whom; the gateway's own self-check is not counted)
  server.addMiddleware([](AsyncWebServerRequest* r, ArMiddlewareNext next) {
    IPAddress from = r->client()->remoteIP();
    if (from != WiFi.localIP()) { httpReqs++; httpLastIp = (uint32_t)from; httpLastMs = millis(); }
    next();
  });
  if (!LittleFS.begin(false)) Serial.println("[FS] LittleFS not mounted (portal files missing: run scripts/build_portal.mjs --flash)");
  server.on("/", HTTP_GET, [](AsyncWebServerRequest* r) { sendPortalFile(r, "/index.html.gz", "text/html"); });
  server.on("/index.html", HTTP_GET, [](AsyncWebServerRequest* r) { sendPortalFile(r, "/index.html.gz", "text/html"); });
  server.on("/add", HTTP_GET, [](AsyncWebServerRequest* r) { sendPortalFile(r, "/index.html.gz", "text/html"); });          // QR link target: the page reads the query itself
  server.on("/jsqr.js", HTTP_GET, [](AsyncWebServerRequest* r) { sendPortalFile(r, "/jsqr.js.gz", "application/javascript"); });
  // captive-portal probes (Android, iOS/macOS, Windows): while the AP is up they must NOT see their "internet works" answer
  static const char* probes[] = {"/generate_204", "/gen_204", "/hotspot-detect.html", "/library/test/success.html", "/connecttest.txt", "/ncsi.txt", "/redirect", "/canonical.html", "/success.txt", "/fwlink"};
  for (const char* p : probes) server.on(p, HTTP_ANY, [](AsyncWebServerRequest* r) { if (viaAp(r)) redirectToPortal(r); else r->send(404); });

  server.on("/api/hello", HTTP_GET, [](AsyncWebServerRequest* r) {
    sendJson(r, 200, "{\"gateway\":\"" + String(gatewayId) + "\",\"mode\":\"" + (setupMode ? "setup" : "run") + "\",\"setupDone\":" + (cfg.setupDone ? "true" : "false") +
                     ",\"adminSet\":" + (cfg.adminSet() ? "true" : "false") + ",\"fw\":\"" + String(AQUA_FW_VERSION >> 8) + "." + String(AQUA_FW_VERSION & 0xFF) + "\"}");
  });
  server.on("/api/login", HTTP_POST, [](AsyncWebServerRequest* r) {
    uint32_t now = millis();
    if (lockUntil && (int32_t)(lockUntil - now) > 0) { sendErr(r, 429, "locked"); return; }
    LOCK(); bool ok = gwCheckAdminPassword(cfg, P(r, "password").c_str()); UNLOCK();
    if (!ok) { if (++loginFails >= 5) { lockUntil = now + 60000UL; loginFails = 0; } sendErr(r, 401, "wrong_password"); return; }
    loginFails = 0; lockUntil = 0; sendJson(r, 200, "{\"token\":\"" + newToken() + "\"}");
  });
  server.on("/api/admin", HTTP_POST, [](AsyncWebServerRequest* r) {
    if (cfg.adminSet() && !authOk(r)) { sendErr(r, 401, "auth"); return; }
    String pw = P(r, "password"); if (pw.length() < 6 || pw.length() > 64) { sendErr(r, 400, "password_6_to_64_chars"); return; }
    LOCK(); gwSetAdminPassword(cfg, pw.c_str()); store.saveAdmin(cfg); UNLOCK();
    for (int i = 0; i < 4; i++) tokens[i][0] = 0;                                         // old tokens die when the password changes
    sendJson(r, 200, "{\"token\":\"" + newToken() + "\"}");
  });
  server.on("/api/status", HTTP_GET, [](AsyncWebServerRequest* r) { NEED_AUTH(r); sendJson(r, 200, statusJson()); });

  server.on("/api/wifi/scan", HTTP_GET, [](AsyncWebServerRequest* r) {
    NEED_AUTH(r);
    int n = WiFi.scanComplete();
    if (n == WIFI_SCAN_FAILED) { WiFi.scanNetworks(true); sendJson(r, 200, "{\"scanning\":true}"); return; }
    if (n == WIFI_SCAN_RUNNING) { sendJson(r, 200, "{\"scanning\":true}"); return; }
    String o = "{\"scanning\":false,\"networks\":["; bool first = true; int used[40]; int nu = 0;
    for (int pass = 0; pass < 30; pass++) {                                              // strongest first, one entry per SSID
      int best = -1, bi = -1;
      for (int i = 0; i < n; i++) { bool done = false; for (int k = 0; k < nu; k++) if (used[k] == i) done = true; if (done || WiFi.SSID(i).length() == 0) continue; if (best == -1 || WiFi.RSSI(i) > best) { best = WiFi.RSSI(i); bi = i; } }
      if (bi < 0) break;
      for (int i = 0; i < n; i++) if (WiFi.SSID(i) == WiFi.SSID(bi)) used[nu < 39 ? nu++ : nu] = i;
      if (!first) o += ","; first = false;
      o += "{\"ssid\":" + jq(WiFi.SSID(bi).c_str()) + ",\"rssi\":" + String(WiFi.RSSI(bi)) + ",\"channel\":" + String(WiFi.channel(bi)) + ",\"open\":" + (WiFi.encryptionType(bi) == WIFI_AUTH_OPEN ? "true" : "false") + "}";
    }
    WiFi.scanDelete(); sendJson(r, 200, o + "]}");
  });
  server.on("/api/wifi", HTTP_POST, [](AsyncWebServerRequest* r) {
    NEED_AUTH(r);
    String ssid = P(r, "ssid"), pass = P(r, "pass");
    if (ssid.length() < 1 || ssid.length() > 32) { sendErr(r, 400, "ssid_1_to_32_chars"); return; }
    if (pass.length() != 0 && (pass.length() < 8 || pass.length() > 64)) { sendErr(r, 400, "password_8_to_64_chars"); return; }
    LOCK(); strlcpy(cfg.ssid, ssid.c_str(), sizeof(cfg.ssid)); strlcpy(cfg.pass, pass.c_str(), sizeof(cfg.pass)); store.saveWifi(cfg); UNLOCK();
    wifiPhase = PH_CONNECTING; wifiErr[0] = 0; wifiReq = true;
    sendJson(r, 200, "{\"ok\":true}");
  });
  server.on("/api/mqtt", HTTP_GET, [](AsyncWebServerRequest* r) {
    NEED_AUTH(r);
    sendJson(r, 200, "{\"host\":" + jq(cfg.mqttHost) + ",\"port\":" + String(cfg.mqttPort) + ",\"user\":" + jq(cfg.mqttUser) + ",\"passSet\":" + (cfg.mqttPass[0] ? "true" : "false") +
                     ",\"tls\":" + (cfg.mqttTls ? "true" : "false") + ",\"insecure\":" + (cfg.tlsInsecure ? "true" : "false") + ",\"prefix\":" + jq(cfg.prefix) +
                     ",\"legacy\":" + (cfg.legacyTopics ? "true" : "false") + ",\"plainCmd\":" + (cfg.allowPlainCmd ? "true" : "false") + "}");
  });
  server.on("/api/mqtt", HTTP_POST, [](AsyncWebServerRequest* r) {
    NEED_AUTH(r);
    String host = P(r, "host"), prefix = hasP(r, "prefix") ? P(r, "prefix") : String(cfg.prefix); int port = hasP(r, "port") ? P(r, "port").toInt() : cfg.mqttPort;
    host.trim(); prefix.trim(); while (prefix.endsWith("/")) prefix.remove(prefix.length() - 1);
    if (host.length() < 1 || host.length() > 64) { sendErr(r, 400, "host_1_to_64_chars"); return; }
    if (port < 1 || port > 65535) { sendErr(r, 400, "bad_port"); return; }
    if (prefix.length() < 1 || prefix.length() > 47 || prefix.indexOf('#') >= 0 || prefix.indexOf('+') >= 0 || prefix.indexOf(' ') >= 0) { sendErr(r, 400, "bad_topic_prefix"); return; }
    LOCK();
    strlcpy(cfg.mqttHost, host.c_str(), sizeof(cfg.mqttHost)); cfg.mqttPort = port; strlcpy(cfg.prefix, prefix.c_str(), sizeof(cfg.prefix));
    if (hasP(r, "user")) strlcpy(cfg.mqttUser, P(r, "user").c_str(), sizeof(cfg.mqttUser));
    if (hasP(r, "pass")) strlcpy(cfg.mqttPass, P(r, "pass").c_str(), sizeof(cfg.mqttPass));              // absent = keep the stored one
    if (hasP(r, "tls")) cfg.mqttTls = P(r, "tls").toInt() != 0;
    if (hasP(r, "insecure")) cfg.tlsInsecure = P(r, "insecure").toInt() != 0;
    if (hasP(r, "legacy")) cfg.legacyTopics = P(r, "legacy").toInt() != 0;
    if (hasP(r, "plainCmd")) cfg.allowPlainCmd = P(r, "plainCmd").toInt() != 0;
    store.saveMqtt(cfg); UNLOCK();
    mqttCfgDirty = true;                                                                                  // main loop reconnects with the new settings
    sendJson(r, 200, "{\"ok\":true}");
  });

  server.on("/api/nodes", HTTP_GET, [](AsyncWebServerRequest* r) { NEED_AUTH(r); sendJson(r, 200, nodesJson()); });
  server.on("/api/nearby", HTTP_GET, [](AsyncWebServerRequest* r) { NEED_AUTH(r); sendJson(r, 200, nearbyJson()); });
  server.on("/api/nodes", HTTP_POST, [](AsyncWebServerRequest* r) {
    NEED_AUTH(r);
    uint8_t mac[6]; if (!aquaMacParse(P(r, "mac").c_str(), mac)) { sendErr(r, 400, "bad_mac"); return; }
    uint8_t type = typeParam(r), rnd[8]; esp_fill_random(rnd, 8);
    LOCK(); int rc = reg.add(type, mac, P(r, "code").c_str(), P(r, "name").c_str(), epochNow(), rnd); int number = rc > 0 ? reg.at(rc).number : 0; if (rc > 0) { g_dirty = true; syncRegistryToCore(); } UNLOCK();
    if (rc <= 0) { const char* m; int code = regErrHttp(rc, &m); sendErr(r, code, m); return; }
    sendJson(r, 200, "{\"ok\":true,\"number\":" + String(number) + ",\"type\":\"" + typeName(type) + "\"}");
  });
  server.on("/api/nodes/replace", HTTP_POST, [](AsyncWebServerRequest* r) {
    NEED_AUTH(r);
    uint8_t mac[6]; if (!aquaMacParse(P(r, "mac").c_str(), mac)) { sendErr(r, 400, "bad_mac"); return; }
    uint8_t type = typeParam(r), rnd[8]; esp_fill_random(rnd, 8); int number = P(r, "number").toInt();
    LOCK(); int rc = reg.replace(type, (uint8_t)number, mac, P(r, "code").c_str(), rnd); if (rc > 0) { g_dirty = true; syncRegistryToCore(); } UNLOCK();
    if (rc <= 0) { const char* m; int code = regErrHttp(rc, &m); sendErr(r, code, m); return; }
    sendJson(r, 200, "{\"ok\":true,\"number\":" + String(number) + "}");
  });

  server.on("/api/setup/finish", HTTP_POST, [](AsyncWebServerRequest* r) {
    NEED_AUTH(r);
    if (!cfg.adminSet()) { sendErr(r, 409, "admin_password_not_set"); return; }
    if (!cfg.ssid[0] || !wifiUp()) { sendErr(r, 409, "wifi_not_connected"); return; }
    if (!cfg.mqttHost[0]) { sendErr(r, 409, "mqtt_not_configured"); return; }
    cfg.setupDone = true; store.saveDone(true); setupMode = false;
    stopApAt = millis() + 4000;                                                                          // let this response reach the phone first
    sendJson(r, 200, "{\"ok\":true,\"ip\":\"" + WiFi.localIP().toString() + "\",\"host\":\"aquaguard.local\"}");
  });
  server.on("/api/setup/enter", HTTP_POST, [](AsyncWebServerRequest* r) { NEED_AUTH(r); enterSetupReq = true; sendJson(r, 200, "{\"ok\":true}"); });
  server.on("/api/factory-reset", HTTP_POST, [](AsyncWebServerRequest* r) { NEED_AUTH(r); factoryResetReq = true; sendJson(r, 200, "{\"ok\":true}"); });

  server.onNotFound([](AsyncWebServerRequest* r) {
    if (r->url().startsWith("/api/nodes/") && (r->method() == HTTP_PATCH || r->method() == HTTP_DELETE)) { handleNodeById(r); return; }
    if (viaAp(r) && r->host() != "192.168.4.1") { redirectToPortal(r); return; }                          // captive portal catch-all
    r->send(404, "text/plain", "not found");
  });
  server.begin();
}
