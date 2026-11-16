/*
===========================================================
  AQUA GUARD - GATEWAY (ESP32 DevKit V1, 4 MB)          Arduino-ESP32 core 3.3.x
  Libraries: AquaProtocol (this repo), PubSubClient, ESP Async WebServer + Async TCP

  First boot: no Wi-Fi saved -> SETUP mode: access point "AquaGuard WiFi" (WPA2, password printed on serial),
  captive portal at http://192.168.4.1 (Wi-Fi + MQTT provisioning, add nodes by photo-QR / QR link / manual entry).
  After "Finish": RUN mode (STA only), portal still reachable on the LAN (http://aquaguard.local) behind the admin password.

  Data path:  node -> ESP-NOW -> AquaGatewayCore (auth, ACK/NACK, dedupe) -> bounded RAM queue -> MQTT.
  The gateway NVS registry is the source of truth for pairing; the backend only mirrors it (retained registry topic)
  and sends signed AQC2 commands.

  Files:  gw_config.h (NVS config, admin password)   gw_api.h (web server, REST API, captive portal)   portal/ (UI source)
  LED (GPIO2, active-HIGH assumed):  SETUP = 1 Hz blink | Wi-Fi down = fast blink | Wi-Fi up, MQTT down = slow blink | all up = solid
  BOOT (GPIO0): SHORT press (hold 1 s .. <5 s, then release) -> the "AquaGuard WiFi" setup AP opens for 10 minutes while the gateway keeps
                running (nothing is erased; use it to add nodes in the field).  Hold 5 s -> full SETUP mode (as before).
  Tasks: loop() = ESP-NOW frames, button, LED, OLED (never blocks on the network).  mqttTask = every MQTT connect/publish (may block, harmless).
         netTask = mDNS (re)registration, HTTP self-check, [NET] status line every 15 s.  The web server runs in the async_tcp task.
===========================================================
*/
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <DNSServer.h>
#include <ESPmDNS.h>
#include <LittleFS.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <esp_random.h>
#include <esp_mac.h>
#include <time.h>
#include <aqua_secrets.h>
#if defined(AQUA_SECRETS_ARE_EXAMPLE)
#error "aqua_secrets.h is the example template: run  python tools/generate_secrets.py"
#endif
#include <AquaProtocol.h>
#include <aqua_registry.h>
#include <aqua_radio_esp32.h>
#include "gw_config.h"
#include "gw_oled.h"

extern const uint8_t rootca_crt_bundle_start[] asm("_binary_x509_crt_bundle_start");
extern const uint8_t rootca_crt_bundle_end[] asm("_binary_x509_crt_bundle_end");

#define LED_PIN              2
#define BTN_PIN              0
#define AQUA_MQTT_QUEUE      192        // telemetry records buffered while MQTT/Wi-Fi is down (~11 KB)
#define HEARTBEAT_PERIOD_MS  15000UL    // backend offline threshold must be > 2x this
#define REGISTRY_REPUBLISH_MS 60000UL
#define WIFI_RETRY_MS        8000UL
#define WIFI_CONNECT_TIMEOUT 20000UL
#define MQTT_RETRY_MIN_MS    2000UL
#define MQTT_RETRY_MAX_MS    30000UL
#define NODE_ONLINE_MS       30000UL
#define BTN_SHORT_MS         1000UL     // BOOT held at least this long (and released before BTN_LONG_MS) = open the setup AP temporarily
#define BTN_LONG_MS          5000UL
#define TEMP_AP_MS           600000UL   // 10 minutes
#define NET_LOG_MS           15000UL

static const uint8_t PSK[16] = AQUA_PSK;
static const uint8_t SITE_KEY[16] = AQUA_CMD_KEY;
static uint8_t cmdKey[16];

// ---- state shared with the web handlers (they run in the async_tcp task: take `mx` for registry/core/config) -----
static SemaphoreHandle_t mx;
#define LOCK()   xSemaphoreTake(mx, portMAX_DELAY)
#define UNLOCK() xSemaphoreGive(mx)
static GwConfig cfg; static GwStore store; static GwRegStore regStore;
static AquaRegistry<GwRegStore> reg;
static AquaGatewayCore core;
static AquaCmd2Guard cmdGuard;
static volatile bool g_dirty = false;                  // registry changed: re-sync into the protocol core
static uint8_t gwMac[6]; static char gatewayId[24], apSsid[] = "AquaGuard WiFi", apPass[16];
static volatile uint32_t rxDropped = 0;
static volatile bool mqttUp = false;                    // written ONLY by mqttTask: everything else reads this, never mqtt.connected()
static volatile bool mqttLostEvt = false, mdnsReq = false;
static SemaphoreHandle_t qmx;                            // protects the telemetry ring (lock order: mx -> qmx, never the reverse)
static volatile uint32_t qSeq = 0, pqSeq = 0;            // number of items removed so far (lets a publisher notice that its item was dropped meanwhile)
static uint32_t tempApUntil = 0;                         // BOOT short press: setup AP open until this time
static volatile uint32_t httpReqs = 0, httpLastIp = 0, httpLastMs = 0;
static volatile bool httpOk = true;

// Wi-Fi / MQTT connection state for the portal
enum Phase : uint8_t { PH_IDLE = 0, PH_CONNECTING, PH_CONNECTED, PH_FAILED };
static volatile Phase wifiPhase = PH_IDLE, mqttPhase = PH_IDLE;
static char wifiErr[64] = "", mqttErr[80] = "";
static uint8_t wifiLastReason = 0;
static uint32_t wifiAttemptStart = 0, wifiDownSince = 0;
static bool setupMode = false, apActive = false, recoveryAp = false;
static uint32_t stopApAt = 0, wifiUpSince = 0;

struct RxItem { uint8_t mac[6]; uint8_t len; int8_t rssi; uint8_t data[AQUA_MAX_FRAME]; };
// 64 frames (was 24): the main loop blocks in MQTT connect/publish for up to the 5 s socket timeout while 5 nodes send ~1 frame/s each => 24 slots overflowed ("dropped from queue")
static const int RX_QUEUE_LEN = 64;
static QueueHandle_t rxQueue;
static AquaTelemetry mqttQ[AQUA_MQTT_QUEUE];
static uint16_t qHead = 0, qCount = 0;
static uint32_t qOverflow = 0, published = 0, publishFail = 0, mqttReconnects = 0, wifiReconnects = 0, channelChanges = 0;
static uint16_t lastBatt[AQUA_MAX_SLOTS + 1];           // last battery reading per slot (AQUA_BATT_NONE = unknown)
static uint32_t lastFrameLedUntil = 0;

static WiFiClient plainClient; static WiFiClientSecure tlsClient; static PubSubClient mqtt;
static char tStatus[128], tHeart[128], tCmdA[128], tCmdB[128], tEvent[128], tInfo[128], tReg[128], tCmdRes[128];
#define PAIR_EVT_Q 8
static AquaPairEvent pairQ[PAIR_EVT_Q]; static uint8_t pqHead = 0, pqCount = 0;
static uint8_t lastChannel = 0, lastConnectedChannel = 0;
static uint32_t lastWifiAttempt = 0, lastMqttAttempt = 0, mqttBackoff = MQTT_RETRY_MIN_MS, lastHeartbeat = 0, lastRegPub = 0, lastRegRev = 0xFFFFFFFF;
static volatile bool mqttCfgDirty = true; static bool timeConfigured = false, restartMqttNow = false;
static DNSServer dns;
static GwOled oled;
static uint32_t bootHeldSince = 0;

static uint8_t currentChannel() { uint8_t p = 0; wifi_second_chan_t s; esp_wifi_get_channel(&p, &s); return p; }
static uint32_t epochNow() { time_t t = time(nullptr); return t > 1700000000 ? (uint32_t)t : 0; }
static bool wifiUp() { return WiFi.status() == WL_CONNECTED; }
static uint8_t gwFlags() { return (wifiUp() ? 1 : 0) | (mqttUp ? 2 : 0); }
// A pairing code may only travel over a TLS connection whose certificate was verified (or an explicit dev flag).
static bool codeChannelOk() { return (cfg.mqttTls && !cfg.tlsInsecure) || cfg.allowPlainCmd; }

static void qPush(const AquaTelemetry& t) {
  xSemaphoreTake(qmx, portMAX_DELAY);
  if (qCount == AQUA_MQTT_QUEUE) { qHead = (qHead + 1) % AQUA_MQTT_QUEUE; qCount--; qOverflow++; qSeq++; }
  mqttQ[(qHead + qCount) % AQUA_MQTT_QUEUE] = t; qCount++;
  xSemaphoreGive(qmx);
}

// ---- ESP-NOW ----------------------------------------------------------------------------------------------------
static bool ensurePeer(const uint8_t mac[6], bool replace) {
  if (esp_now_is_peer_exist(mac)) { if (!replace) return true; esp_now_del_peer(mac); }
  esp_now_peer_info_t p = {};
  memcpy(p.peer_addr, mac, 6); p.channel = 0; p.ifidx = WIFI_IF_STA;       // channel 0 = follow the current radio channel
  static const uint8_t bc[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
  if (memcmp(mac, bc, 6) != 0) {
#if AQUA_ESPNOW_ENCRYPT
    static uint8_t lmk[16] = AQUA_LMK; p.encrypt = true; memcpy(p.lmk, lmk, 16);
#endif
  }
  return esp_now_add_peer(&p) == ESP_OK;
}
static void sendOut(const AquaGwOut& o) { if (!esp_now_is_peer_exist(o.toMac)) ensurePeer(o.toMac, false); esp_now_send(o.toMac, o.frame, o.len); }

static volatile uint32_t rawRx = 0;                                       // every callback, before any filter (diagnostics)
static void onEspNowRecv(const esp_now_recv_info_t* info, const uint8_t* data, int len) {   // Wi-Fi task: copy only
  rawRx++;
  if (!info || len <= 0 || len > AQUA_MAX_FRAME) return;
  RxItem it; memcpy(it.mac, info->src_addr, 6); it.len = (uint8_t)len;
  it.rssi = info->rx_ctrl ? info->rx_ctrl->rssi : 0; memcpy(it.data, data, len);
  if (xQueueSend(rxQueue, &it, 0) != pdTRUE) rxDropped++;                // counted and reported in the heartbeat
}
static void onEspNowSent(const wifi_tx_info_t*, esp_now_send_status_t) {}

// ---- registry -> protocol core --------------------------------------------------------------------------------
// The registry (NVS) decides; the core only follows.  Caller holds `mx`.
static void syncRegistryToCore() { aquaRegSyncCore(reg, core, [](const AquaGwOut& o) { sendOut(o); }); g_dirty = false; }

// ---- JSON helpers ------------------------------------------------------------------------------------------------
static String jq(const char* s) {                                         // JSON string literal
  String o = "\""; for (; *s; s++) { unsigned char c = *s; if (c == '"' || c == '\\') { o += '\\'; o += (char)c; } else if (c < 0x20) o += ' '; else o += (char)c; } o += '"'; return o;
}
static const char* typeName(uint8_t t) { return t == AQUA_DEV_RAIN ? "rain" : "flow"; }

// One node as JSON (shared by the portal API and the retained MQTT registry).  Caller holds `mx`.
static void nodeJson(String& o, uint8_t s, uint32_t nowMs) {
  const AquaRegEntry& e = reg.at(s); const AquaGatewayCore::Node& n = core.node(s);
  char mac[18]; aquaMacFormat(e.mac, mac);
  bool online = n.keyed && n.everSeen && (uint32_t)(nowMs - n.lastSeenMs) < NODE_ONLINE_MS;
  o += "{\"number\":" + String(e.number) + ",\"type\":\"" + typeName(e.devType) + "\",\"name\":" + jq(e.name) + ",\"mac\":\"" + mac + "\"";
  o += ",\"state\":\""; o += (e.state == 2 ? "paired" : "pending"); o += "\",\"online\":"; o += online ? "true" : "false";
  o += ",\"lastSeenEpoch\":" + String(n.lastSeenEpoch) + ",\"ageS\":" + String(n.everSeen ? (long)((nowMs - n.lastSeenMs) / 1000) : -1L);
  o += ",\"batteryMv\":"; if (e.devType == AQUA_DEV_FLOW && lastBatt[s] != AQUA_BATT_NONE) o += String(lastBatt[s]); else o += "null";
  o += ",\"rssi\":" + String(n.rssi) + ",\"pplX10\":" + String(e.pplX10) + ",\"txReduce\":" + ((aquaTxDbmX10(e.cfgFlags) <= 85) ? "true" : "false") + ",\"txDbmX10\":" + String(aquaTxDbmX10(e.cfgFlags));
  o += ",\"missing\":" + String((unsigned long)n.rx.lost) + ",\"addedEpoch\":" + String(e.addedEpoch) + "}";
}
static String registryJson(uint32_t nowMs) {                              // caller holds `mx`
  String o = "{\"schema\":3,\"gateway\":\"" + String(gatewayId) + "\",\"rev\":" + String(reg.revision()) + ",\"epoch\":" + String(epochNow()) + ",\"nodes\":[";
  bool first = true;
  for (uint8_t s = 1; s <= AQUA_MAX_SLOTS; s++) if (reg.at(s).used) { if (!first) o += ","; nodeJson(o, s, nowMs); first = false; }
  return o + "]}";
}

#include "gw_api.h"

// ---- Wi-Fi ---------------------------------------------------------------------------------------------------------
static const char* reasonText(uint8_t r) {
  switch (r) {
    case 201: return "network not found";
    case 2: case 15: case 202: case 204: case 205: case 6: case 7: return "wrong password or rejected by the access point";
    case 200: return "beacon timeout (signal lost)";
    default: return "could not connect";
  }
}
static void startAp(bool recovery) {
  if (apActive) { recoveryAp = recoveryAp && recovery; return; }
  WiFi.softAP(apSsid, apPass, 1, 0, 4); apActive = true; recoveryAp = recovery;
  dns.start(53, "*", WiFi.softAPIP());
  Serial.printf("[AP] \"%s\" open (password %s) %s\n", apSsid, apPass, recovery ? "- RECOVERY: Wi-Fi unreachable" : "- SETUP");
}
static void stopAp() {
  if (!apActive) return;
  dns.stop(); WiFi.softAPdisconnect(true); apActive = false; recoveryAp = false; tempApUntil = 0;
  Serial.println("[AP] closed");
}
static void wifiBegin(bool fullScan) {
  if (!cfg.ssid[0]) return;
  WiFi.disconnect(false, false);
  if (!fullScan && lastConnectedChannel) WiFi.begin(cfg.ssid, cfg.pass, lastConnectedChannel); else WiFi.begin(cfg.ssid, cfg.pass);
  wifiAttemptStart = millis(); wifiPhase = PH_CONNECTING; wifiErr[0] = 0;
  Serial.printf("[WiFi] connecting to \"%s\" ...\n", cfg.ssid);
}
static void onWiFiEvent(arduino_event_id_t ev, arduino_event_info_t info) {
  if (ev == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
    uint8_t ch = currentChannel();
    if (lastConnectedChannel && ch != lastConnectedChannel) channelChanges++;
    lastConnectedChannel = ch; wifiPhase = PH_CONNECTED; wifiErr[0] = 0; wifiUpSince = millis(); wifiDownSince = 0;
    Serial.printf("[WiFi] connected %s RSSI %d channel %u\n", WiFi.localIP().toString().c_str(), WiFi.RSSI(), ch);
    if (!timeConfigured) { configTime(0, 0, "pool.ntp.org", "time.google.com"); timeConfigured = true; }
    mdnsReq = true;                                                                  // netTask (re)registers aquaguard.local for THIS ip
  } else if (ev == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
    wifiLastReason = info.wifi_sta_disconnected.reason;
    Serial.printf("[WiFi] disconnected, reason %u (%s)\n", wifiLastReason, reasonText(wifiLastReason));
    if (!wifiDownSince) wifiDownSince = millis();
  } else if (ev == ARDUINO_EVENT_WIFI_STA_LOST_IP) Serial.println("[WiFi] lost IP");
}
static void manageWiFi(uint32_t now) {
  static bool was = false; bool up = wifiUp();
  if (up != was) { if (!up) { wifiReconnects++; oled.event("Wi-Fi lost", millis()); wifiPhase = PH_FAILED; strlcpy(wifiErr, "connection lost: ", sizeof(wifiErr)); strlcat(wifiErr, reasonText(wifiLastReason), sizeof(wifiErr)); } was = up; }
  if (up) {
    if (wifiPhase != PH_CONNECTED) wifiPhase = PH_CONNECTED;
    if (recoveryAp && !setupMode && (uint32_t)(now - wifiUpSince) > 120000UL) stopAp();          // router is back: drop the recovery AP
    return;
  }
  if (!cfg.ssid[0]) return;
  if (!wifiDownSince) wifiDownSince = now;
  if (wifiPhase == PH_CONNECTING && (uint32_t)(now - wifiAttemptStart) > WIFI_CONNECT_TIMEOUT) {
    wifiPhase = PH_FAILED; strlcpy(wifiErr, reasonText(wifiLastReason), sizeof(wifiErr));        // portal shows the reason; retries continue
  }
  if ((uint32_t)(now - wifiAttemptStart) >= WIFI_CONNECT_TIMEOUT && (uint32_t)(now - lastWifiAttempt) >= WIFI_RETRY_MS && wifiPhase != PH_CONNECTING) {         // never restart an attempt that is still running
    lastWifiAttempt = now; bool wasFailed = wifiPhase == PH_FAILED; wifiBegin(wasFailed); if (wasFailed) wifiPhase = PH_FAILED;   // keep showing the failure reason while retrying
  }
  // recovery: Wi-Fi unreachable for N minutes in RUN mode -> reopen the setup AP (queueing continues meanwhile)
  if (cfg.setupDone && !apActive && cfg.recoveryMin && (uint32_t)(now - wifiDownSince) > (uint32_t)cfg.recoveryMin * 60000UL) startAp(true);
}

// ---- MQTT ----------------------------------------------------------------------------------------------------------
static void buildTopics() {
  snprintf(tStatus, sizeof(tStatus), "%s/gateway/status", cfg.prefix);
  snprintf(tHeart, sizeof(tHeart), "%s/gateway/heartbeat", cfg.prefix);
  snprintf(tCmdA, sizeof(tCmdA), "%s/gateway/cmd", cfg.prefix);
  snprintf(tCmdB, sizeof(tCmdB), "%s/%s/gateway/cmd", cfg.prefix, gatewayId);
  snprintf(tEvent, sizeof(tEvent), "%s/pairing/event", cfg.prefix);
  snprintf(tInfo, sizeof(tInfo), "%s/%s/gateway/info", cfg.prefix, gatewayId);
  snprintf(tReg, sizeof(tReg), "%s/%s/nodes/registry", cfg.prefix, gatewayId);
  snprintf(tCmdRes, sizeof(tCmdRes), "%s/%s/gateway/cmd/result", cfg.prefix, gatewayId);
}
static void applyMqttConfig() {
  buildTopics();
  if (mqtt.connected()) mqtt.disconnect();
  mqttUp = false;
  if (cfg.mqttTls) {
    if (cfg.tlsInsecure) tlsClient.setInsecure(); else tlsClient.setCACertBundle(rootca_crt_bundle_start, rootca_crt_bundle_end - rootca_crt_bundle_start);
    mqtt.setClient(tlsClient);
  } else mqtt.setClient(plainClient);
  mqtt.setServer(cfg.mqttHost, cfg.mqttPort);
  mqttCfgDirty = false; mqttBackoff = MQTT_RETRY_MIN_MS; lastMqttAttempt = 0;
  mqttPhase = cfg.mqttHost[0] ? PH_CONNECTING : PH_IDLE; mqttErr[0] = 0;
}
static String infoJson() {
  String o = "{\"schema\":3,\"gateway\":\"" + String(gatewayId) + "\",\"fw\":\"" + String(AQUA_FW_VERSION >> 8) + "." + String(AQUA_FW_VERSION & 0xFF) + "\",\"proto\":" + String(AQUA_PROTO_VERSION);
  o += ",\"mode\":\""; o += (setupMode ? "setup" : "run"); o += "\",\"setupDone\":"; o += cfg.setupDone ? "true" : "false";
  o += ",\"ip\":\"" + (wifiUp() ? WiFi.localIP().toString() : String("")) + "\",\"channel\":" + String(currentChannel()) + ",\"rssi\":" + String(wifiUp() ? WiFi.RSSI() : 0);
  o += ",\"timeSynced\":"; o += epochNow() ? "true" : "false"; o += ",\"epoch\":" + String(epochNow()) + ",\"tls\":" + (cfg.mqttTls ? "true" : "false") + ",\"tlsVerified\":" + ((cfg.mqttTls && !cfg.tlsInsecure) ? "true" : "false");
  o += ",\"remoteAdd\":"; o += (codeChannelOk() && epochNow()) ? "true" : "false"; o += ",\"uptime\":" + String(millis() / 1000) + "}";
  return o;
}
static void publishInfo(bool force) {
  if (!mqttUp) return;
  LOCK(); String reg_ = registryJson(millis()); uint32_t rev = reg.revision(); UNLOCK();
  if (force || rev != lastRegRev || millis() - lastRegPub > REGISTRY_REPUBLISH_MS) {
    mqtt.publish(tReg, reg_.c_str(), true); mqtt.publish(tInfo, infoJson().c_str(), true);
    lastRegRev = rev; lastRegPub = millis();
  }
}
static void cmdResult(const char* what, int rc, int number) {
  char b[96]; snprintf(b, sizeof(b), "{\"result\":\"%s\",\"rc\":%d,\"number\":%d,\"epoch\":%lu}", what, rc, number, (unsigned long)epochNow());
  if (mqtt.connected()) mqtt.publish(tCmdRes, b);
  Serial.printf("[CMD] %s (rc %d)\n", what, rc);
}
static void onMqttMessage(char* topic, uint8_t* payload, unsigned int len) {
  if (strcmp(topic, tCmdA) != 0 && strcmp(topic, tCmdB) != 0) return;
  AquaCmd2 c;
  LOCK();
  int rc = aquaCmd2Parse((const char*)payload, len, SITE_KEY, epochNow(), cmdGuard, &c);
  if (rc != AQUA_C2_OK) {
    UNLOCK();
    cmdResult(rc == AQUA_C2_NO_TIME ? "gateway_time_not_synced" : rc == AQUA_C2_TAG ? "bad_signature" : rc == AQUA_C2_STALE ? "stale_or_future" : rc == AQUA_C2_REPLAY ? "replay" : "bad_format", rc, 0);
    return;
  }
  if ((c.op == 'A' || c.op == 'P') && !codeChannelOk()) { UNLOCK(); cmdResult("tls_required", -100, 0); return; }   // never accept a pairing code over plain MQTT
  uint8_t rnd[8]; esp_fill_random(rnd, 8);
  int r = aquaCmd2Apply(reg, c, epochNow(), rnd);
  if (r > 0) { g_dirty = true; syncRegistryToCore(); }
  int number = r > 0 ? reg.at(r).number : (c.op == 'R' ? c.number : 0);
  UNLOCK();
  const char* what = r > 0 ? "ok" : r == AQUA_REG_DUP_MAC ? "duplicate_mac" : r == AQUA_REG_NOT_FOUND ? "not_found" : r == AQUA_REG_FULL ? "registry_full" : r == AQUA_REG_BAD_CODE ? "bad_code" : "bad_argument";
  if (c.op == 'R' && r > 0) number = c.number;
  cmdResult(what, r, number);
  publishInfo(true);
}
static void manageMqtt(uint32_t now) {            // mqttTask only (connect / publish may block here: that is why it has its own task)
  if (mqttCfgDirty) applyMqttConfig();
  bool c = mqtt.connected(); mqttUp = c;
  if (!cfg.mqttHost[0] || !wifiUp()) return;
  static bool mqttWasUp = false;
  if (c) { mqtt.loop(); mqttUp = mqtt.connected(); mqttPhase = PH_CONNECTED; mqttWasUp = true; return; }
  if (mqttWasUp) { mqttWasUp = false; mqttLostEvt = true; }
  if (mqttPhase == PH_CONNECTED) mqttPhase = PH_CONNECTING;
  if (lastMqttAttempt && (uint32_t)(now - lastMqttAttempt) < mqttBackoff) return;
  lastMqttAttempt = now; if (!now) lastMqttAttempt = 1;
  char cid[48]; snprintf(cid, sizeof(cid), "%s-%04lx", gatewayId, (unsigned long)(esp_random() & 0xFFFF));
  const char* user = cfg.mqttUser[0] ? cfg.mqttUser : nullptr; const char* pass = cfg.mqttUser[0] ? cfg.mqttPass : nullptr;
  Serial.printf("[MQTT] connecting to %s:%u%s ...\n", cfg.mqttHost, cfg.mqttPort, cfg.mqttTls ? (cfg.tlsInsecure ? " (TLS, certificate NOT verified)" : " (TLS)") : " (plaintext)");
  if (mqtt.connect(cid, user, pass, tStatus, 1, true, "offline")) {
    Serial.println("[MQTT] connected"); mqttUp = true;
    mqttReconnects++; mqttBackoff = MQTT_RETRY_MIN_MS; mqttPhase = PH_CONNECTED; mqttErr[0] = 0;
    mqtt.publish(tStatus, "online", true);
    mqtt.subscribe(tCmdA, 1); mqtt.subscribe(tCmdB, 1);
    lastHeartbeat = now - HEARTBEAT_PERIOD_MS; lastRegRev = 0xFFFFFFFF;                             // heartbeat + registry right away
  } else {
    int st = mqtt.state(); mqttPhase = PH_FAILED;
    char tl[48] = ""; if (cfg.mqttTls) tlsClient.lastError(tl, sizeof(tl));
    snprintf(mqttErr, sizeof(mqttErr), "%s%s%s", st == 4 ? "bad username or password" : st == 5 ? "not authorized" : st == -2 ? "cannot reach the broker (host/port/TLS)" : st == -4 ? "timeout" : "connection failed", tl[0] ? ": " : "", tl);
    Serial.printf("[MQTT] failed, state=%d %s\n", st, tl);
    mqttBackoff = min(mqttBackoff * 2, MQTT_RETRY_MAX_MS);
  }
}

// ---- publishing ------------------------------------------------------------------------------------------------------
static void collectPairEvents() {
  AquaPairEvent e;
  while (core.popEvent(&e)) {
    char om[24];
    if (e.kind == AQUA_PE_PAIR_OK && e.firstTime && AquaGatewayCore::slotOk(e.slot)) {
      reg.markPaired(e.slot); Serial.printf("[PAIR] %s %u joined\n", typeName(reg.at(e.slot).devType), reg.at(e.slot).number);
      snprintf(om, sizeof(om), "%s %u joined", reg.at(e.slot).devType == AQUA_DEV_RAIN ? "Rain" : "Node", reg.at(e.slot).number); oled.event(om, millis());
    } else if (e.kind == AQUA_PE_PAIR_FAIL) oled.event("Pairing failed", millis());
    else if (e.kind == AQUA_PE_DISCOVERED) { snprintf(om, sizeof(om), "Nearby: %02X:%02X", e.mac[4], e.mac[5]); oled.event(om, millis()); }   // MAC tail only, never a code
    if (pqCount == PAIR_EVT_Q) { pqHead = (pqHead + 1) % PAIR_EVT_Q; pqCount--; pqSeq++; }
    pairQ[(pqHead + pqCount) % PAIR_EVT_Q] = e; pqCount++;
  }
}
static void drainPairEvents() {                    // mqttTask: never holds `mx` while publishing
  if (!mqttUp) return;
  uint32_t ep = epochNow(); if (ep == 0) return;                                                    // signed events need real time
  char line[220];
  for (int k = 0; k < PAIR_EVT_Q; k++) {
    AquaPairEvent e; uint8_t number; uint32_t seq;
    LOCK();
    if (pqCount == 0) { UNLOCK(); return; }
    e = pairQ[pqHead]; seq = pqSeq;
    number = (AquaGatewayCore::slotOk(e.slot) && reg.at(e.slot).used) ? reg.at(e.slot).number : 0;
    UNLOCK();
    int n = aquaPairEventFormat(line, sizeof(line), cmdKey, e, number, ep);
    if (n > 0 && mqtt.publish(tEvent, line)) { LOCK(); if (pqSeq == seq && pqCount > 0) { pqHead = (pqHead + 1) % PAIR_EVT_Q; pqCount--; pqSeq++; } UNLOCK(); } else return;
  }
}
static void drainMqttQueue() {                     // mqttTask: the ring is copied out under `qmx`, the (possibly slow) publish happens without any lock
  if (!mqttUp) return;
  static char json[768], topic[160], topic2[160];
  for (int i = 0; i < 16; i++) {
    AquaTelemetry t; uint32_t seq;
    xSemaphoreTake(qmx, portMAX_DELAY);
    if (qCount == 0) { xSemaphoreGive(qmx); return; }
    t = mqttQ[qHead]; seq = qSeq;
    xSemaphoreGive(qmx);
    int n = aquaJsonTelemetry(json, sizeof(json), t);
    bool rain = t.devType == AQUA_DEV_RAIN;
    snprintf(topic, sizeof(topic), rain ? "%s/%s/rain/%u" : "%s/%s/node/%u", cfg.prefix, gatewayId, t.number);   // new, per gateway
    if (n > 0 && n < (int)sizeof(json) && mqtt.publish(topic, json)) {
      if (cfg.legacyTopics) {                                                                         // v1 backend topics
        if (rain) snprintf(topic2, sizeof(topic2), "%s/rainwater", cfg.prefix); else snprintf(topic2, sizeof(topic2), "%s/node%u", cfg.prefix, t.number);
        mqtt.publish(topic2, json);
      }
      published++;
      xSemaphoreTake(qmx, portMAX_DELAY);
      if (qSeq == seq && qCount > 0) { qHead = (qHead + 1) % AQUA_MQTT_QUEUE; qCount--; qSeq++; }     // unless an overflow already dropped it
      xSemaphoreGive(qmx);
    } else { publishFail++; return; }
  }
}
static void publishHeartbeat(uint32_t now) {
  if (!mqttUp || (uint32_t)(now - lastHeartbeat) < HEARTBEAT_PERIOD_MS) return;
  lastHeartbeat = now;
  AquaGwHealth g; memset(&g, 0, sizeof(g));
  g.gatewayId = gatewayId; g.uptimeS = now / 1000; g.epoch = epochNow(); g.freeHeap = ESP.getFreeHeap();
  g.wifiUp = wifiUp(); g.mqttUp = true; g.rssi = g.wifiUp ? WiFi.RSSI() : 0; g.channel = currentChannel();
  g.qDepth = qCount; g.qCapacity = AQUA_MQTT_QUEUE; g.qOverflow = qOverflow; g.published = published; g.publishFail = publishFail;
  g.mqttReconnects = mqttReconnects; g.wifiReconnects = wifiReconnects; g.channelChanges = channelChanges; g.rxDropped = rxDropped;
  static char json[7168];                                  // 12 nodes x ~430 B + header: a too-small buffer would silently stop the heartbeat
  LOCK(); int n = aquaJsonGatewayHeartbeat(json, sizeof(json), g, core, now); UNLOCK();     // `core` belongs to loop(): read it under `mx`
  if (n > 0 && n < (int)sizeof(json)) mqtt.publish(tHeart, json);
  mqtt.publish(tStatus, "online", true);
}

// ---- LED + BOOT button ----------------------------------------------------------------------------------------------------
static void updateLed(uint32_t now) {
  bool on;
  if (setupMode) on = (now / 500) % 2 == 0;
  else if (!wifiUp()) on = (now / 125) % 2 == 0;
  else if (!mqttUp) on = (now / 1000) % 2 == 0;
  else on = true;
  if ((int32_t)(lastFrameLedUntil - now) > 0) on = !on;                                            // brief inversion per node frame
  digitalWrite(LED_PIN, on ? HIGH : LOW);
}
static void enterSetupMode() { setupMode = true; tempApUntil = 0; startAp(false); Serial.println("[MODE] SETUP (AP open)"); }
// BOOT short press: open the setup AP for 10 minutes, keep running, erase nothing (adding nodes in the field).  Pressing again re-arms the 10 minutes.
static void openTempAp(uint32_t now) {
  if (setupMode) return;                                                 // the AP is open anyway
  tempApUntil = now + TEMP_AP_MS; if (!tempApUntil) tempApUntil = 1;
  startAp(false);
  oled.event("Setup AP open 10 min", now);
  Serial.printf("[AP] BOOT short press: setup AP \"%s\" open for 10 min (password %s, http://192.168.4.1); nothing erased, MQTT keeps running\n", apSsid, apPass);
}
static void handleButton(uint32_t now) {
  static uint8_t hint = 0;
  bool down = digitalRead(BTN_PIN) == LOW;
  if (down) {
    if (!bootHeldSince) { bootHeldSince = now ? now : 1; hint = 0; }
    uint32_t held = now - bootHeldSince;
    if (held >= BTN_LONG_MS) { if (hint < 2) { hint = 2; if (!setupMode) enterSetupMode(); } }
    else if (held >= BTN_SHORT_MS && hint < 1) { hint = 1; if (!setupMode) oled.event("Release: open AP", now); }
  } else {
    if (bootHeldSince) { uint32_t held = now - bootHeldSince; if (held >= BTN_SHORT_MS && held < BTN_LONG_MS) openTempAp(now); }
    bootHeldSince = 0;
  }
}
// ---- background tasks ----------------------------------------------------------------------------------------------------
// MQTT (connect, publish, keep-alive) lives here: a slow broker or DNS lookup blocks THIS task only, never the ESP-NOW receive loop or the web server.
static void mqttTask(void*) {
  for (;;) {
    uint32_t now = millis();
    manageMqtt(now);
    drainPairEvents(); drainMqttQueue(); publishHeartbeat(now); publishInfo(false);
    vTaskDelay(pdMS_TO_TICKS(4));
  }
}
// Network watch: aquaguard.local for the CURRENT ip, an INFORMATIONAL HTTP self-check, and one [NET] line every 15 s (the line to read when the page does not load).
// The web server is started ONCE in setup() (gwApiBegin) and is never stopped, ended or re-begun: an earlier version restarted it after failed self-checks and that
// left the listener dead (ERR_CONNECTION_REFUSED).  The check only reports; it never touches the server.
static bool httpSelfCheck(IPAddress to) {
  WiFiClient c; bool ok = false;
  if (c.connect(to, 80, 2000)) {
    c.print("GET /api/hello HTTP/1.0\r\nHost: self\r\n\r\n");
    uint32_t t0 = millis(); while ((uint32_t)(millis() - t0) < 2500 && !c.available()) vTaskDelay(pdMS_TO_TICKS(10));
    if (c.available()) { String l = c.readStringUntil('\n'); ok = l.indexOf(" 200") > 0; }
    c.stop();
  }
  return ok;
}
static void netTask(void*) {
  uint32_t lastLog = 0, lastProbe = 0; bool mdnsOk = false, lastHttp = true; IPAddress mdnsIp((uint32_t)0);
  for (;;) {
    vTaskDelay(pdMS_TO_TICKS(1000));
    uint32_t now = millis(); bool up = wifiUp();
    if (up && (mdnsReq || !mdnsOk || mdnsIp != WiFi.localIP())) {
      mdnsReq = false; MDNS.end();
      mdnsOk = MDNS.begin("aquaguard"); if (mdnsOk) { MDNS.addService("http", "tcp", 80); mdnsIp = WiFi.localIP(); }
      Serial.printf("[mDNS] aquaguard.local %s (%s)\n", mdnsOk ? "registered" : "FAILED", WiFi.localIP().toString().c_str());
    }
    if (!up) mdnsOk = false;
    if (up && now > 12000 && (uint32_t)(now - lastProbe) >= 20000) {
      lastProbe = now; bool ok = httpSelfCheck(IPAddress(127, 0, 0, 1)) || httpSelfCheck(WiFi.localIP()); httpOk = ok;
      if (ok != lastHttp) { Serial.printf("[HTTP] self-check %s (informational: the server is never restarted)\n", ok ? "ok again" : "FAILED"); lastHttp = ok; }
    }
    if ((uint32_t)(now - lastLog) >= NET_LOG_MS) {
      lastLog = now;
      long since = httpLastMs ? (long)((now - httpLastMs) / 1000) : -1; IPAddress li(httpLastIp);
      Serial.printf("[NET] wifi=%s ip=%s gw=%s rssi=%d ch=%u ap=%s%s mdns=%s http=%s reqs=%lu last=%s(%lds ago) mqtt=%s q=%u heap=%u min=%u\n",
                    up ? "up" : "DOWN", up ? WiFi.localIP().toString().c_str() : "-", up ? WiFi.gatewayIP().toString().c_str() : "-", up ? WiFi.RSSI() : 0, currentChannel(),
                    apActive ? "on" : "off", tempApUntil ? "(10min)" : "", mdnsOk ? "ok" : "-", httpOk ? "ok" : "FAIL", (unsigned long)httpReqs, li.toString().c_str(), since,
                    mqttUp ? "up" : "DOWN", (unsigned)qCount, (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getMinFreeHeap());
    }
  }
}

// ---- main ---------------------------------------------------------------------------------------------------------------------
void setup() {
  pinMode(LED_PIN, OUTPUT); pinMode(BTN_PIN, INPUT_PULLUP);
  Serial.begin(115200); delay(300);
  oled.begin();                                                            // optional: the gateway carries on without a display
  mx = xSemaphoreCreateMutex(); qmx = xSemaphoreCreateMutex();
  WiFi.persistent(false); WiFi.mode(WIFI_AP_STA); WiFi.setAutoReconnect(false); WiFi.setSleep(false); WiFi.onEvent(onWiFiEvent);
  esp_read_mac(gwMac, ESP_MAC_WIFI_STA);
  snprintf(gatewayId, sizeof(gatewayId), "gw-%02X%02X%02X", gwMac[3], gwMac[4], gwMac[5]);
  { uint8_t in[8] = {'A', 'P', gwMac[1], gwMac[2], gwMac[3], gwMac[4], gwMac[5], gwMac[0]}; uint64_t h = aquaSipHash24(PSK, in, 8); uint8_t b[8]; memcpy(b, &h, 8);
    char code[AQUA_CODE_LEN + 1]; aquaCodeFromRandom(b, code); memcpy(apPass, code, 10); apPass[10] = 0; }   // 10 symbols, derived from MAC + the site PSK
  { uint8_t k[16] = AQUA_PSK; if (aquaKeyIsWeak(k)) { Serial.println("FATAL: PSK is all-zero (example template). Run tools/generate_secrets.py"); for (;;) delay(1000); } }
  aquaCmdKey(SITE_KEY, cmdKey);

  store.begin(); store.load(cfg, AQUA_DEFAULT_MQTT_TOPIC_PREFIX, AQUA_DEFAULT_MQTT_PORT);
  regStore.begin(); reg.begin(&regStore);
  memset(lastBatt, 0xFF, sizeof(lastBatt));
  Serial.printf("\n=== AQUA GUARD GATEWAY %s  fw %u.%u  proto v%d ===\nSTA MAC %02X:%02X:%02X:%02X:%02X:%02X\n", gatewayId, AQUA_FW_VERSION >> 8, AQUA_FW_VERSION & 0xFF, AQUA_PROTO_VERSION,
                gwMac[0], gwMac[1], gwMac[2], gwMac[3], gwMac[4], gwMac[5]);
  Serial.printf("Setup AP: \"%s\"  password: %s   (portal http://192.168.4.1)\n", apSsid, apPass);
  Serial.printf("Config: %s, Wi-Fi %s, MQTT %s, %u node(s) in the registry\n", cfg.setupDone ? "setup complete" : "SETUP NOT DONE", cfg.ssid[0] ? cfg.ssid : "(none)", cfg.mqttHost[0] ? cfg.mqttHost : "(none)", reg.count());

  rxQueue = xQueueCreate(RX_QUEUE_LEN, sizeof(RxItem));
  core.begin(PSK, gwMac, esp_random() | 1u);
  if (esp_now_init() != ESP_OK) { Serial.println("[ESP-NOW] init failed - restarting"); delay(1000); ESP.restart(); }
#if AQUA_ESPNOW_ENCRYPT
  static uint8_t pmk[16] = AQUA_PMK; esp_now_set_pmk(pmk);
#endif
  esp_now_register_recv_cb(onEspNowRecv); esp_now_register_send_cb(onEspNowSent);
  static const uint8_t bc[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF}; ensurePeer(bc, false);
  LOCK(); syncRegistryToCore(); UNLOCK();

  mqtt.setCallback(onMqttMessage); mqtt.setBufferSize(7500); mqtt.setKeepAlive(45); mqtt.setSocketTimeout(5);
  setupMode = !cfg.setupDone;
  if (setupMode) startAp(false); else WiFi.softAPdisconnect(true);
  gwApiBegin();
  if (cfg.ssid[0]) { lastWifiAttempt = millis(); wifiBegin(false); }
  xTaskCreatePinnedToCore(mqttTask, "mqtt", 12288, nullptr, 1, nullptr, 0);   // 12 KB: the TLS handshake needs it
  xTaskCreatePinnedToCore(netTask, "net", 6144, nullptr, 1, nullptr, 0);
}

void loop() {
  uint32_t now = millis();
  handleButton(now);
  if (wifiReq) { wifiReq = false; wifiLastReason = 0; wifiBegin(true); }                       // set by the portal (POST /api/wifi)
  if (enterSetupReq) { enterSetupReq = false; enterSetupMode(); }
  if (factoryResetReq) { factoryResetReq = false; LOCK(); store.clearAll(); regStore.clear(); UNLOCK(); restartAt = now + 800; Serial.println("[RESET] factory reset: config + registry erased"); }
  if (restartAt && (int32_t)(now - restartAt) >= 0) ESP.restart();
  if (apActive) dns.processNextRequest();
  if (stopApAt && (int32_t)(now - stopApAt) >= 0) { stopApAt = 0; stopAp(); }
  if (tempApUntil && !setupMode && (int32_t)(now - tempApUntil) >= 0) { Serial.println("[AP] 10 minutes are over: closing the setup AP"); stopAp(); oled.event("Setup AP closed", now); }
  if (mqttLostEvt) { mqttLostEvt = false; oled.event("MQTT lost", now); }
  manageWiFi(now);
  uint8_t ch = currentChannel(); if (ch != lastChannel) { Serial.printf("[RADIO] channel now %u\n", ch); lastChannel = ch; }

  { static uint32_t lastRxLog = 0;                                          // [RX] line every 5 s: are frames arriving, and why rejected?
    if ((uint32_t)(now - lastRxLog) >= 5000) { lastRxLog = now; LOCK(); const auto& tt = core.totals();
      Serial.printf("[RX] ch=%u frames=%lu rejected=%lu badTag=%lu badValue=%lu unknownMac=%lu discovery=%lu announces=%lu dropped=%lu raw=%lu\n", ch, (unsigned long)tt.framesRx, (unsigned long)tt.rejected,
                    (unsigned long)tt.badTag, (unsigned long)tt.badValue, (unsigned long)tt.unknownMac, (unsigned long)tt.discoveryRx, (unsigned long)tt.announces, (unsigned long)rxDropped, (unsigned long)rawRx); UNLOCK(); } }
  LOCK();
  if (g_dirty) syncRegistryToCore();
  RxItem it; int guard = 0;
  while (guard++ < RX_QUEUE_LEN && xQueueReceive(rxQueue, &it, 0) == pdTRUE) {
    AquaGwOut out; AquaTelemetry tel; bool tv = false;
    bool reply = core.onFrame(it.mac, it.data, it.len, now, epochNow(), ch, it.rssi, wifiUp() ? WiFi.RSSI() : 0, gwFlags(), &out, &tel, &tv);
    for (uint8_t s = 1; s <= AQUA_MAX_SLOTS; s++) { AquaGatewayCore::Node& nd = core.node(s); if (nd.peerDirty) { ensurePeer(nd.mac, true); nd.peerDirty = false; } }
    if (reply) sendOut(out);
    if (tv) {
      qPush(tel); lastFrameLedUntil = now + 40;
      if (tel.devType == AQUA_DEV_FLOW && AquaGatewayCore::slotOk(tel.nodeId)) { AquaFlowPayload f; memcpy(&f, tel.payload, sizeof(f)); lastBatt[tel.nodeId] = f.batteryMv; }
    }
  }
  collectPairEvents();
  for (uint8_t s = 1; s <= AQUA_MAX_SLOTS; s++) { AquaGwOut out; if (core.pollNack(s, now, &out)) sendOut(out); }
  UNLOCK();

  if (oled.due(now)) {                                                      // status screen: 2 Hz at most, main loop only
    bool apShown = setupMode || tempApUntil;                                                                // SETUP, or the 10-minute AP opened with the BOOT button
    OledView v; v.setup = apShown; v.apSsid = apSsid; v.apPass = apShown ? apPass : "";                    // the AP password exists on screen only while the setup AP is open
    if (tempApUntil && !setupMode) { int32_t left = (int32_t)(tempApUntil - now); v.apLeftS = left > 0 ? (uint16_t)(left / 1000) : 0; }
    v.wifiUp = wifiUp(); v.wifiTrying = wifiPhase == PH_CONNECTING; v.channel = currentChannel(); v.rssi = v.wifiUp ? WiFi.RSSI() : 0;
    if (v.wifiUp) strlcpy(v.ip, WiFi.localIP().toString().c_str(), sizeof(v.ip));
    v.mqttUp = mqttUp; v.timeSynced = epochNow() != 0; v.queue = qCount;
    LOCK(); for (uint8_t s = 1; s <= AQUA_MAX_SLOTS; s++) if (reg.at(s).used) { v.nodesTotal++; const AquaGatewayCore::Node& n = core.node(s); if (n.keyed && n.everSeen && (uint32_t)(now - n.lastSeenMs) < NODE_ONLINE_MS) v.nodesOnline++; } UNLOCK();
    oled.draw(now, v);
  }
  updateLed(now);
  delay(1);
}
