/*
 * Gateway status OLED (128x64 I2C, SH1106 by default - most 1.3" modules; -DOLED_DRIVER_SSD1306 for 0.96" SSD1306).
 * Wiring: VCC 3V3, GND, SDA GPIO21, SCL GPIO22, address 0x3C (0x3D is tried too).
 *
 * Rules: called from the main loop ONLY (never from the async web handlers); it redraws at most twice a second;
 * if no display answers at boot it logs once and the gateway carries on without it.
 * Privacy: the setup-AP password is shown ONLY in SETUP mode; pairing codes are never shown (events show a MAC tail only).
 */
#pragma once
#include <Arduino.h>
#include <Wire.h>
#include <U8g2lib.h>
#include <qrcode.h>                         // esp_qrcode of the ESP32 core (also used by the node banner)

#ifndef OLED_ENABLE
#define OLED_ENABLE 1
#endif
#define OLED_SDA 21
#define OLED_SCL 22
#define OLED_REFRESH_MS   500UL             // 2 updates per second at most
#define OLED_EVENT_MS     5000UL
#define OLED_PAGE_MS      4000UL            // SETUP: text page <-> Wi-Fi join QR

#if defined(OLED_DRIVER_SSD1306)
static U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE, OLED_SCL, OLED_SDA);
#else
static U8G2_SH1106_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE, OLED_SCL, OLED_SDA);
#endif

struct OledView {
  bool setup = false;                        // SETUP mode (password may be shown)
  const char* apSsid = ""; const char* apPass = "";
  bool wifiUp = false, wifiTrying = false; uint8_t channel = 0; int rssi = 0; char ip[16] = "";
  bool mqttUp = false, timeSynced = false;
  uint8_t nodesOnline = 0, nodesTotal = 0; uint16_t queue = 0;
  uint16_t apLeftS = 0;                      // > 0: the setup AP was opened with the BOOT button and closes in this many seconds
};

class GwOled {
 public:
  void begin() {
#if OLED_ENABLE
    Wire.begin(OLED_SDA, OLED_SCL); Wire.setClock(400000);
    uint8_t addr = 0;
    for (uint8_t a : {0x3C, 0x3D}) { Wire.beginTransmission(a); if (Wire.endTransmission() == 0) { addr = a; break; } }
    if (!addr) { Serial.println("[OLED] no display found at 0x3C/0x3D - continuing without it"); return; }
    u8g2.setI2CAddress(addr << 1);
    if (!u8g2.begin()) { Serial.println("[OLED] init failed - continuing without it"); return; }
    ok_ = true; u8g2.setFont(u8g2_font_6x10_tf);
    Serial.printf("[OLED] display at 0x%02X (%s)\n", addr,
#if defined(OLED_DRIVER_SSD1306)
                  "SSD1306"
#else
                  "SH1106"
#endif
    );
    u8g2.clearBuffer(); u8g2.drawStr(0, 12, "AQUA GUARD gateway"); u8g2.drawStr(0, 26, "starting..."); u8g2.sendBuffer();
#endif
  }
  bool present() const { return ok_; }
  // Short message shown for ~5 s (copy only, safe to call from anywhere in the main loop).
  void event(const char* msg, uint32_t now) { strlcpy(msg_, msg, sizeof(msg_)); msgUntil_ = now + OLED_EVENT_MS; if (!msgUntil_) msgUntil_ = 1; force_ = true; }

  // Cheap when nothing is due: the view is only built (needs locks) when `due()` says so.
  bool due(uint32_t now) const { return ok_ && ((uint32_t)(now - last_) >= OLED_REFRESH_MS || force_); }
  void draw(uint32_t now, const OledView& v) {
    if (!ok_) return;
    last_ = now; force_ = false;
    u8g2.clearBuffer();
    bool msgOn = msgUntil_ && (int32_t)(msgUntil_ - now) > 0; if (!msgOn) msgUntil_ = 0;
    if (v.setup) drawSetup(now, v, msgOn); else drawRun(v, msgOn);
    u8g2.sendBuffer();                                                    // ~25 ms at 400 kHz, twice a second at most
  }

 private:
  void banner() {                                                         // inverted bar with the current event
    u8g2.setDrawColor(1); u8g2.drawBox(0, 0, 128, 12); u8g2.setDrawColor(0); u8g2.drawStr(2, 10, msg_); u8g2.setDrawColor(1);
  }
  void drawRun(const OledView& v, bool msgOn) {
    char l[28];
    if (msgOn) banner(); else u8g2.drawStr(0, 10, "AQUA GUARD  RUN");
    if (v.wifiUp) snprintf(l, sizeof(l), "WiFi ok ch%u %ddBm", v.channel, v.rssi); else snprintf(l, sizeof(l), v.wifiTrying ? "WiFi connecting..." : "WiFi DOWN");
    u8g2.drawStr(0, 23, l);
    if (v.wifiUp) { snprintf(l, sizeof(l), "IP %s", v.ip); u8g2.drawStr(0, 33, l); } else u8g2.drawStr(0, 33, "IP: - (no Wi-Fi)");   // where to open the portal (always the CURRENT address)
    snprintf(l, sizeof(l), "MQTT %s  Time %s", v.mqttUp ? "up" : "DOWN", v.timeSynced ? "ok" : "--"); u8g2.drawStr(0, 43, l);
    snprintf(l, sizeof(l), "Nodes %u/%u  Q%u", v.nodesOnline, v.nodesTotal, v.queue); u8g2.drawStr(0, 53, l);
    u8g2.drawStr(0, 63, "or aquaguard.local");
  }
  void drawSetup(uint32_t now, const OledView& v, bool msgOn) {
    bool qrPage = !msgOn && ((now / OLED_PAGE_MS) % 2) == 1 && v.apPass[0];       // an event always shows on the text page
    if (msgOn) { banner(); }
    if (qrPage) { drawQr(v); return; }
    char l[28];
    if (!msgOn) u8g2.drawStr(0, 10, v.apSsid);
    snprintf(l, sizeof(l), "Pass: %s", v.apPass); u8g2.drawStr(0, 24, l);
    u8g2.drawStr(0, 36, "http://192.168.4.1");
    if (v.apLeftS) snprintf(l, sizeof(l), "AP open %u:%02u left", v.apLeftS / 60, v.apLeftS % 60); else snprintf(l, sizeof(l), "SETUP  WiFi %s", v.wifiUp ? "ok" : v.wifiTrying ? "..." : "-");
    u8g2.drawStr(0, 50, l);
    if (v.wifiUp) { snprintf(l, sizeof(l), "LAN %s", v.ip); u8g2.drawStr(0, 62, l); } else u8g2.drawStr(0, 62, "next: scan to join");
  }
  void drawQr(const OledView& v) {
    if (!qrReady_) makeQr(v);
    int sc = 2, total = qrN_ * sc;
    if (qrN_ > 0 && total <= 62) {
      u8g2.setDrawColor(1); u8g2.drawBox(0, 0, total + 4, total + 4);                                   // white quiet zone
      u8g2.setDrawColor(0);
      for (int y = 0; y < qrN_; y++) for (int x = 0; x < qrN_; x++) if (qrBits_[y * 33 + x]) u8g2.drawBox(2 + x * sc, 2 + y * sc, sc, sc);
      u8g2.setDrawColor(1);
      u8g2.drawStr(70, 12, "Scan to"); u8g2.drawStr(70, 24, "join the"); u8g2.drawStr(70, 36, "setup"); u8g2.drawStr(70, 48, "Wi-Fi");
    } else { u8g2.drawStr(0, 24, "QR unavailable"); u8g2.drawStr(0, 36, "use the password"); }
  }
  static void qrCapture(esp_qrcode_handle_t q) {
    GwOled* self = inst(); int n = esp_qrcode_get_size(q); if (n > 33) { self->qrN_ = 0; return; }
    self->qrN_ = n; for (int y = 0; y < n; y++) for (int x = 0; x < n; x++) self->qrBits_[y * 33 + x] = esp_qrcode_get_module(q, x, y);
  }
  static GwOled*& inst() { static GwOled* g = nullptr; return g; }
  void makeQr(const OledView& v) {                                        // standard Wi-Fi join QR: WIFI:T:WPA;S:<ssid>;P:<pass>;;
    char txt[80]; snprintf(txt, sizeof(txt), "WIFI:T:WPA;S:%s;P:%s;;", v.apSsid, v.apPass);
    inst() = this; qrN_ = 0;
    esp_qrcode_config_t cfg = ESP_QRCODE_CONFIG_DEFAULT(); cfg.display_func = qrCapture; cfg.max_qrcode_version = 3; cfg.qrcode_ecc_level = ESP_QRCODE_ECC_LOW;
    if (esp_qrcode_generate(&cfg, txt) != ESP_OK) qrN_ = 0;
    qrReady_ = true;
  }
  bool ok_ = false, force_ = false, qrReady_ = false;
  uint32_t last_ = 0, msgUntil_ = 0; char msg_[24] = ""; int qrN_ = 0; uint8_t qrBits_[33 * 33];
};
