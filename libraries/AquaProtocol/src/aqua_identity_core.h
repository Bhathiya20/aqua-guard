/*
 * Node identity logic (pure C++, host-testable): the pairing code lives in persistent storage and is printed on
 * the node's QR sticker, so it must survive everything except an explicit, confirmed "regen-code".
 *
 *   factoryReset()  clears the pairing state (paired flag, calibration, TX flag).  The CODE IS KEPT.
 *   regenCode()     new code (the old sticker becomes useless) - only reachable through the serial command
 *                   "regen-code" + confirmation (see aqua_identity_esp32.h).
 *
 * Store interface (NVS on the device, a fake in the host tests):
 *   bool getStr(const char* key, char* out, size_t cap);   void putStr(const char* key, const char* v);
 *   bool getBool(const char* key, bool def);                void putBool(const char* key, bool v);
 *   uint16_t getU16(const char* key, uint16_t def);         void putU16(const char* key, uint16_t v);
 *   uint8_t  getU8(const char* key, uint8_t def);           void putU8(const char* key, uint8_t v);
 *   void remove(const char* key);
 */
#pragma once
#include <stdint.h>
#include <string.h>
#include "aqua_join.h"

template <class Store>
class AquaIdentityCore {
 public:
  // rnd8: fresh random bytes, used only when no valid code is stored yet.  Returns true if a NEW code was created.
  bool begin(Store* st, uint8_t devType, const uint8_t mac[6], const uint8_t rnd8[8]) {
    st_ = st; devType_ = devType; memcpy(mac_, mac, 6);
    char stored[AQUA_CODE_LEN + 8] = {0};
    bool created = false;
    if (!st_->getStr("code", stored, sizeof(stored)) || !aquaCodeNormalize(stored, code_)) {
      aquaCodeFromRandom(rnd8, code_);
      st_->putStr("code", code_);
      created = true;
    }
    aquaPairingKey(code_, mac_, kp_);
    return created;
  }

  // Clears pairing state, KEEPS the code (and therefore the sticker).
  void factoryReset() {
    st_->remove("paired"); st_->remove("pplx10"); st_->remove("cfgflags");
  }

  // New code: invalidates the printed sticker and every gateway registration of this node.
  void regenCode(const uint8_t rnd8[8]) {
    aquaCodeFromRandom(rnd8, code_);
    st_->putStr("code", code_);
    aquaPairingKey(code_, mac_, kp_);
    factoryReset();                                   // a node with a new code is a new node: nothing of the old pairing applies
  }

  const uint8_t* mac() const { return mac_; }
  const uint8_t* pairingKey() const { return kp_; }
  const char* code() const { return code_; }
  uint8_t type() const { return devType_; }

  bool wasPaired() { return st_->getBool("paired", false); }
  void setPaired(bool v) { st_->putBool("paired", v); }
  uint16_t pplX10(uint16_t def) { return st_->getU16("pplx10", def); }
  void setPplX10(uint16_t v) { st_->putU16("pplx10", v); }
  uint8_t cfgFlags() { return st_->getU8("cfgflags", 0); }
  void setCfgFlags(uint8_t v) { st_->putU8("cfgflags", v); }

  int qrPayload(char* out, size_t cap, bool compact = false) const { return aquaQrBuild(mac_, code_, devType_, compact, out, cap); }

 private:
  Store* st_ = nullptr;
  uint8_t devType_ = AQUA_DEV_FLOW;
  uint8_t mac_[6] = {0};
  uint8_t kp_[16] = {0};
  char code_[AQUA_CODE_LEN + 1] = {0};
};

// Serial command parser for "regen-code" with a confirmation step (pure logic; the device feeds it lines).
class AquaRegenConfirm {
 public:
  enum Result { NONE = 0, ASK_CONFIRM = 1, DO_REGEN = 2, CANCELLED = 3 };
  static const uint32_t WINDOW_MS = 30000;
  Result onLine(const char* line, uint32_t nowMs) {
    char b[40]; size_t n = 0;
    while (*line == ' ' && n < sizeof(b)) line++;
    while (*line && n < sizeof(b) - 1) b[n++] = *line++;
    while (n && (b[n - 1] == ' ' || b[n - 1] == '\r' || b[n - 1] == '\n')) n--;
    b[n] = 0;
    if (strcmp(b, "regen-code") == 0) { armedAt_ = nowMs ? nowMs : 1; return ASK_CONFIRM; }
    if (strcmp(b, "regen-code confirm") == 0) {
      bool ok = armedAt_ && (uint32_t)(nowMs - armedAt_) <= WINDOW_MS;
      armedAt_ = 0;
      return ok ? DO_REGEN : CANCELLED;               // "confirm" without a fresh "regen-code" does nothing
    }
    if (armedAt_ && n) { armedAt_ = 0; return CANCELLED; }   // any other input cancels
    return NONE;
  }
 private:
  uint32_t armedAt_ = 0;
};
