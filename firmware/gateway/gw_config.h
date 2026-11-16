/*
 * Gateway configuration in NVS (namespace "aqgw") + the NVS store used by the node registry (namespace "aqreg").
 * Nothing secret is compiled into the gateway except the per-site keys of aqua_secrets.h.
 * Passwords are never returned by any API: only "set / not set".
 */
#pragma once
#include <Arduino.h>
#include <Preferences.h>
#include <mbedtls/sha256.h>
#include <esp_random.h>

struct GwConfig {
  char ssid[33] = "", pass[65] = "";
  char mqttHost[65] = "", mqttUser[33] = "", mqttPass[65] = "", prefix[48] = "";
  uint16_t mqttPort = 1883;
  bool mqttTls = false;
  bool tlsInsecure = false;         // TLS without certificate verification (encrypts, does not authenticate the broker)
  bool setupDone = false;
  bool legacyTopics = false;         // also publish <prefix>/node<N> and <prefix>/rainwater (what the v1 backend reads)
  bool allowPlainCmd = false;       // DEV ONLY: accept add/replace commands (pairing code inside) over a non-TLS broker
  uint8_t recoveryMin = 10;         // minutes without Wi-Fi before the setup AP re-opens
  char adminSalt[17] = "", adminHash[65] = "";
  bool adminSet() const { return adminHash[0] != 0; }
};

class GwStore {
 public:
  void begin() { p_.begin("aqgw", false); }
  void load(GwConfig& c, const char* defaultPrefix, uint16_t defaultPort) {
    p_.getString("ssid", c.ssid, sizeof(c.ssid)); p_.getString("pass", c.pass, sizeof(c.pass));
    p_.getString("mhost", c.mqttHost, sizeof(c.mqttHost)); p_.getString("muser", c.mqttUser, sizeof(c.mqttUser));
    p_.getString("mpass", c.mqttPass, sizeof(c.mqttPass)); p_.getString("prefix", c.prefix, sizeof(c.prefix));
    if (!c.prefix[0]) strlcpy(c.prefix, defaultPrefix, sizeof(c.prefix));
    c.mqttPort = p_.getUShort("mport", defaultPort); c.mqttTls = p_.getBool("mtls", false); c.tlsInsecure = p_.getBool("tlsins", false);
    c.setupDone = p_.getBool("done", false); c.legacyTopics = p_.getBool("leg2", false);   /* new key: an old stored "on" no longer re-enables the retired v1 topics */ c.allowPlainCmd = p_.getBool("plaincmd", false);
    c.recoveryMin = p_.getUChar("recmin", 10);
    p_.getString("asalt", c.adminSalt, sizeof(c.adminSalt)); p_.getString("ahash", c.adminHash, sizeof(c.adminHash));
  }
  void saveWifi(const GwConfig& c) { p_.putString("ssid", c.ssid); p_.putString("pass", c.pass); }
  void saveMqtt(const GwConfig& c) {
    p_.putString("mhost", c.mqttHost); p_.putString("muser", c.mqttUser); p_.putString("mpass", c.mqttPass); p_.putString("prefix", c.prefix);
    p_.putUShort("mport", c.mqttPort); p_.putBool("mtls", c.mqttTls); p_.putBool("tlsins", c.tlsInsecure);
    p_.putBool("leg2", c.legacyTopics); p_.putBool("plaincmd", c.allowPlainCmd);
  }
  void saveDone(bool v) { p_.putBool("done", v); }
  void saveRecovery(uint8_t m) { p_.putUChar("recmin", m); }
  void saveAdmin(const GwConfig& c) { p_.putString("asalt", c.adminSalt); p_.putString("ahash", c.adminHash); }
  void clearAll() { p_.clear(); }
 private:
  Preferences p_;
};

// Salted SHA-256 (1000 rounds) of the admin password, hex.  The password itself is never stored.
static inline void gwHashPassword(const char* salt, const char* pw, char outHex[65]) {
  uint8_t h[32]; String in = String(salt) + pw;
  mbedtls_sha256((const uint8_t*)in.c_str(), in.length(), h, 0);
  for (int i = 0; i < 1000; i++) { uint8_t b[32 + 16]; memcpy(b, h, 32); memcpy(b + 32, salt, 16); mbedtls_sha256(b, sizeof(b), h, 0); }
  static const char* H = "0123456789abcdef";
  for (int i = 0; i < 32; i++) { outHex[2 * i] = H[h[i] >> 4]; outHex[2 * i + 1] = H[h[i] & 15]; }
  outHex[64] = 0;
}
static inline void gwSetAdminPassword(GwConfig& c, const char* pw) {
  uint8_t r[8]; esp_fill_random(r, 8); static const char* H = "0123456789abcdef";
  for (int i = 0; i < 8; i++) { c.adminSalt[2 * i] = H[r[i] >> 4]; c.adminSalt[2 * i + 1] = H[r[i] & 15]; }
  c.adminSalt[16] = 0; gwHashPassword(c.adminSalt, pw, c.adminHash);
}
static inline bool gwCheckAdminPassword(const GwConfig& c, const char* pw) {
  if (!c.adminSet()) return false;
  char h[65]; gwHashPassword(c.adminSalt, pw, h);
  uint8_t d = 0; for (int i = 0; i < 64; i++) d |= (uint8_t)(h[i] ^ c.adminHash[i]);       // constant time
  return d == 0;
}

// Store adapter for AquaRegistry (NVS namespace "aqreg").
class GwRegStore {
 public:
  void begin() { p_.begin("aqreg", false); }
  bool getStr(const char* k, char* out, size_t cap) { if (!p_.isKey(k)) return false; return p_.getString(k, out, cap) > 0; }
  void putStr(const char* k, const char* v) { p_.putString(k, v); }
  uint8_t getU8(const char* k, uint8_t d) { return p_.getUChar(k, d); }
  void putU8(const char* k, uint8_t v) { p_.putUChar(k, v); }
  void remove(const char* k) { if (p_.isKey(k)) p_.remove(k); }
  void clear() { p_.clear(); }
 private:
  Preferences p_;
};
