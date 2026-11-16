/*
 * Node identity on ESP32 / ESP32-C3 (Arduino-ESP32 core 3.x): pairing code in NVS, pairing key, QR payload.
 *
 *   - The pairing code is generated ONCE from the hardware RNG at first boot and kept in NVS.  It is printed on
 *     the node's QR sticker, so it survives reboots, firmware updates AND factory reset (15 s BOOT hold: that only
 *     clears the pairing state).  The only way to get a new code is the serial command  regen-code  followed by
 *     regen-code confirm  (prints the new code + QR and a warning that a new sticker is needed).
 *   - Serial banner at every boot: firmware, MAC, code, the QR payload and an ASCII QR (esp_qrcode of the core:
 *     include <qrcode.h> BEFORE this header; without it only the text payload is printed).
 *
 * Known limitation (documented): the code is stored in NVS in plaintext.  NVS flash encryption would protect it
 * against a physical flash read-out; copying one node's flash only exposes THAT node (per-node keys).
 */
#pragma once
#if !defined(ESP32)
#error "aqua_identity_esp32.h is for ESP32 targets only"
#endif
#include <Arduino.h>
#include <Preferences.h>
#include <esp_mac.h>
#include <esp_random.h>
#include <esp_system.h>
#include "aqua_protocol.h"
#include "aqua_join.h"
#include "aqua_identity_core.h"

class AquaPrefsStore {                                    // Store for AquaIdentityCore backed by NVS
 public:
  bool open() { return p_.begin("aquanode", false); }
  bool getStr(const char* k, char* out, size_t cap) { if (!p_.isKey(k)) return false; return p_.getString(k, out, cap) > 0; }
  void putStr(const char* k, const char* v) { p_.putString(k, v); }
  bool getBool(const char* k, bool d) { return p_.getBool(k, d); }
  void putBool(const char* k, bool v) { p_.putBool(k, v); }
  uint16_t getU16(const char* k, uint16_t d) { return p_.getUShort(k, d); }
  void putU16(const char* k, uint16_t v) { p_.putUShort(k, v); }
  uint8_t getU8(const char* k, uint8_t d) { return p_.getUChar(k, d); }
  void putU8(const char* k, uint8_t v) { p_.putUChar(k, v); }
  void remove(const char* k) { if (p_.isKey(k)) p_.remove(k); }
 private:
  Preferences p_;
};

class AquaIdentity {
 public:
  // devType: AQUA_DEV_FLOW / AQUA_DEV_RAIN.  Returns false if NVS is unusable.
  bool begin(uint8_t devType) {
    uint8_t mac[6]; esp_read_mac(mac, ESP_MAC_WIFI_STA);   // the MAC ESP-NOW frames carry (STA interface)
    if (!store_.open()) return false;
    uint8_t rnd[8]; esp_fill_random(rnd, sizeof(rnd));
    core_.begin(&store_, devType, mac, rnd);
    return true;
  }

  const uint8_t* mac() const { return core_.mac(); }
  const uint8_t* pairingKey() const { return core_.pairingKey(); }
  const char* code() const { return core_.code(); }       // 12 symbols
  uint8_t type() const { return core_.type(); }
  bool wasPaired() { return core_.wasPaired(); }
  void setPaired(bool v) { core_.setPaired(v); }
  uint16_t pplX10(uint16_t def) { return core_.pplX10(def); }
  void setPplX10(uint16_t v) { core_.setPplX10(v); }
  uint8_t cfgFlags() { return core_.cfgFlags(); }
  void setCfgFlags(uint8_t v) { core_.setCfgFlags(v); }
  int qrPayload(char* out, size_t cap, bool compact = false) const { return core_.qrPayload(out, cap, compact); }

  // Factory reset: forget pairing state + calibration, KEEP the code (the sticker stays valid). The caller restarts.
  void factoryReset() { core_.factoryReset(); }

  // Call from loop(): handles the serial commands  regen-code / regen-code confirm.  Returns true when a NEW code
  // was generated (the caller restarts so the new identity is used everywhere).
  bool pollSerial(Stream& in, Print& out, const char* title) {
    while (in.available()) {
      char c = (char)in.read();
      if (c == '\n' || c == '\r') {
        line_[n_] = 0; bool had = n_ > 0; n_ = 0;
        if (!had) continue;
        if (strcmp(line_, "info") == 0) { regen_.onLine(line_, millis() | 1u); printBanner(out, title); continue; }   // reprint on demand
        switch (regen_.onLine(line_, millis() | 1u)) {
          case AquaRegenConfirm::ASK_CONFIRM:
            out.println("[ID] regen-code: this creates a NEW pairing code. The printed sticker will no longer work and the node");
            out.println("[ID] must be added again. To proceed type  regen-code confirm  within 30 s (anything else cancels).");
            break;
          case AquaRegenConfirm::CANCELLED: out.println("[ID] regen-code cancelled."); break;
          case AquaRegenConfirm::DO_REGEN: {
            uint8_t rnd[8]; esp_fill_random(rnd, sizeof(rnd));
            core_.regenCode(rnd);
            out.println("\n[ID] !!! NEW PAIRING CODE CREATED - the old sticker is now USELESS. Print a new one !!!");
            printBanner(out, title);
            return true;
          }
          default: break;
        }
      } else if (n_ < sizeof(line_) - 1) line_[n_++] = c;
    }
    return false;
  }

  void printBanner(Print& out, const char* title) {
    char macs[18], shown[15], url[112], compact[64];
    aquaMacFormat(core_.mac(), macs); aquaCodeFormat(core_.code(), shown);
    qrPayload(url, sizeof(url), false); qrPayload(compact, sizeof(compact), true);
    out.printf("\n=== %s  fw %u.%u  proto v%d ===\n", title, AQUA_FW_VERSION >> 8, AQUA_FW_VERSION & 0xFF, AQUA_PROTO_VERSION);
    out.printf("Reset reason:  %s (uptime %lu s)\n", resetReasonText(), (unsigned long)(millis() / 1000));
    out.printf("MAC:           %s\n", macs);
    out.printf("Pairing code:  %s\n", shown);
    out.printf("QR payload:    %s\n", url);
    out.printf("Compact form:  %s\n", compact);
#if defined(ESP_QRCODE_CONFIG_DEFAULT)
    printAsciiQr(out, url);
#endif
    out.println("(commands: info = reprint this banner | regen-code = new code.  sticker: python tools/make_node_labels.py --serial-log <captured output>)\n");
  }

  static const char* resetReasonText() {
    switch (esp_reset_reason()) {
      case ESP_RST_POWERON: return "POWERON (power applied / regulator)";
      case ESP_RST_EXT: return "EXT (reset pin)";
      case ESP_RST_SW: return "SW (esp_restart: sketch restart)";
      case ESP_RST_PANIC: return "PANIC (crash / exception)";
      case ESP_RST_INT_WDT: return "INT_WDT (interrupt watchdog)";
      case ESP_RST_TASK_WDT: return "TASK_WDT (task watchdog)";
      case ESP_RST_WDT: return "WDT (other watchdog)";
      case ESP_RST_DEEPSLEEP: return "DEEPSLEEP";
      case ESP_RST_BROWNOUT: return "BROWNOUT (supply dipped)";
      case ESP_RST_USB: return "USB (host reset the chip)";
      case ESP_RST_SDIO: return "SDIO";
      default: return "UNKNOWN";
    }
  }

 private:
#if defined(ESP_QRCODE_CONFIG_DEFAULT)
  static Print*& qrOut() { static Print* o = nullptr; return o; }
  // Uses the QR encoder that ships with the ESP32 Arduino core (esp_qrcode): no extra library to install.
  static void qrDisplay(esp_qrcode_handle_t qr) {
    Print& out = *qrOut(); int size = esp_qrcode_get_size(qr);
    // Light modules are drawn as filled blocks, so on a DARK terminal the dark modules are the background:
    // that is the polarity phone cameras expect (a quiet zone of 1 module is added).
    for (int y = -1; y <= size; y += 2) {
      for (int x = -1; x <= size; x++) {
        bool darkT = (y >= 0 && y < size && x >= 0 && x < size) && esp_qrcode_get_module(qr, x, y);
        bool darkB = (y + 1 >= 0 && y + 1 < size && x >= 0 && x < size) && esp_qrcode_get_module(qr, x, y + 1);
        bool lightT = !darkT, lightB = !darkB;
        // UTF-8 blocks: U+2588 full, U+2580 upper half, U+2584 lower half
        out.print(lightT ? (lightB ? "\xE2\x96\x88" : "\xE2\x96\x80") : (lightB ? "\xE2\x96\x84" : " "));
      }
      out.println();
    }
  }
  static void printAsciiQr(Print& out, const char* text) {
    qrOut() = &out;
    esp_qrcode_config_t cfg = ESP_QRCODE_CONFIG_DEFAULT();
    cfg.display_func = qrDisplay; cfg.max_qrcode_version = 6; cfg.qrcode_ecc_level = ESP_QRCODE_ECC_LOW;
    if (esp_qrcode_generate(&cfg, text) != ESP_OK) out.println("(QR preview failed)");
  }
#endif
  AquaPrefsStore store_;
  AquaIdentityCore<AquaPrefsStore> core_;
  AquaRegenConfirm regen_;
  char line_[48]; size_t n_ = 0;
};
