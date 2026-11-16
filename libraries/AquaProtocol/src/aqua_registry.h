/*
 * Gateway node registry + signed command handling (pure C++, host-testable).
 *
 * The registry is the SOURCE OF TRUTH for pairing (the backend only mirrors it).  It lives in the gateway's NVS
 * through a small Store interface (a fake in the host tests):
 *     bool getStr(key,out,cap)  void putStr(key,v)  uint8_t getU8(key,def)  void putU8(key,v)  void remove(key)
 *
 * Entry (one NVS string per radio slot, key "n<slot>"):
 *     number|type|mac12|code12|salt16|pplX10|flags|state|addedEpoch|name
 *   - number  display number within the type (Node N / Rain N). Assigned in order from a per-type counter that
 *             never goes backwards, so a removed number is NOT handed to a different MAC.  Only "replace" keeps a
 *             number for new hardware.
 *   - slot    internal ESP-NOW address (1..AQUA_MAX_SLOTS), never shown.
 *   - code    the pairing code in PLAINTEXT (documented limitation; NVS flash encryption would protect it).
 *
 * Signed commands (backend -> gateway), text line, SipHash tag over everything before the last field:
 *   AQC2 <ts> <nonce24hex> <op> <type> <number> <mac12|-> <pplX10|-> <flags|-> <namehex|-> <codeEnc24hex|-> <tag16hex>
 *   op: A add | N rename | R remove | P replace | C calibrate
 *   codeEnc = ChaCha20(key = KDF(site key,'E',0)||KDF(site key,'E',1), nonce = the 12 nonce bytes, counter 0) of the
 *   12 ASCII code symbols.  Tag key = Kcmd = KDF(site key,'C').  Rules: the gateway needs NTP time (fails closed
 *   without it), |ts - now| <= 300 s, ts never older than the last accepted command, the nonce must be new.
 */
#pragma once
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "aqua_protocol.h"
#include "aqua_join.h"
#include "aqua_chacha.h"
#include "aqua_pair_text.h"
#include "aqua_gateway_core.h"

struct AquaRegEntry {
  bool     used;
  uint8_t  slot, number, devType, state;       // state: 1 pending, 2 paired
  uint8_t  mac[6];
  char     code[AQUA_CODE_LEN + 1];
  uint8_t  salt[8];
  uint16_t pplX10;                             // 0 = node default (450/L)
  uint8_t  cfgFlags;
  uint32_t addedEpoch;
  char     name[25];
};

enum AquaRegRc : int {
  AQUA_REG_OK = 0, AQUA_REG_FULL = -1, AQUA_REG_DUP_MAC = -2, AQUA_REG_BAD_ARG = -3, AQUA_REG_NOT_FOUND = -4, AQUA_REG_BAD_CODE = -5
};

// Keep names printable, no field separator, max 24 bytes.
static inline void aquaRegCleanName(const char* in, char out[25]) {
  size_t n = 0;
  for (; in && *in && n < 24; in++) { unsigned char c = (unsigned char)*in; if (c >= 0x20 && c != '|' && c != 0x7F) out[n++] = (char)c; }
  while (n && out[n - 1] == ' ') n--;
  out[n] = 0;
}

template <class Store>
class AquaRegistry {
 public:
  void begin(Store* st) {
    st_ = st; memset(e_, 0, sizeof(e_));
    for (uint8_t s = 1; s <= AQUA_MAX_SLOTS; s++) load(s);
    next_[0] = st_->getU8("nextFlow", 1); next_[1] = st_->getU8("nextRain", 1);
    for (uint8_t s = 0; s < AQUA_MAX_SLOTS; s++) if (e_[s].used) {          // counters never fall behind stored entries
      uint8_t& nx = next_[e_[s].devType == AQUA_DEV_RAIN ? 1 : 0]; if (e_[s].number >= nx) nx = e_[s].number + 1;
    }
  }
  const AquaRegEntry& at(uint8_t slot) const { return e_[slot - 1]; }
  uint8_t count() const { uint8_t c = 0; for (auto& x : e_) if (x.used) c++; return c; }
  uint32_t revision() const { return rev_; }                                 // bumps on every change (republish trigger)

  int findByMac(const uint8_t mac[6]) const { for (uint8_t i = 0; i < AQUA_MAX_SLOTS; i++) if (e_[i].used && !memcmp(e_[i].mac, mac, 6)) return i + 1; return -1; }
  int findByNumber(uint8_t type, uint8_t number) const { for (uint8_t i = 0; i < AQUA_MAX_SLOTS; i++) if (e_[i].used && e_[i].devType == type && e_[i].number == number) return i + 1; return -1; }

  // Add a node the owner typed / scanned.  rnd8 = fresh random bytes (the salt).  Returns the slot or a negative AquaRegRc.
  int add(uint8_t type, const uint8_t mac[6], const char* codeIn, const char* name, uint32_t epoch, const uint8_t rnd8[8]) {
    if (type != AQUA_DEV_FLOW && type != AQUA_DEV_RAIN) return AQUA_REG_BAD_ARG;
    char code[AQUA_CODE_LEN + 1]; if (!aquaCodeNormalize(codeIn ? codeIn : "", code)) return AQUA_REG_BAD_CODE;
    if (findByMac(mac) > 0) return AQUA_REG_DUP_MAC;
    int slot = 0; for (uint8_t i = 0; i < AQUA_MAX_SLOTS; i++) if (!e_[i].used) { slot = i + 1; break; }
    uint8_t& nx = next_[type == AQUA_DEV_RAIN ? 1 : 0];
    if (!slot || nx >= 250) return AQUA_REG_FULL;
    AquaRegEntry& x = e_[slot - 1]; memset(&x, 0, sizeof(x));
    x.used = true; x.slot = slot; x.number = nx++; x.devType = type; x.state = 1;
    memcpy(x.mac, mac, 6); memcpy(x.code, code, AQUA_CODE_LEN + 1); memcpy(x.salt, rnd8, 8); x.addedEpoch = epoch;
    char nm[25]; aquaRegCleanName(name, nm);
    if (!nm[0]) snprintf(nm, sizeof(nm), "%s %u", type == AQUA_DEV_RAIN ? "Rain" : "Node", x.number);
    memcpy(x.name, nm, sizeof(nm));
    st_->putU8(type == AQUA_DEV_RAIN ? "nextRain" : "nextFlow", nx);
    save(slot); rev_++; return slot;
  }
  int remove(uint8_t type, uint8_t number) {
    int s = findByNumber(type, number); if (s < 0) return AQUA_REG_NOT_FOUND;
    char key[8]; snprintf(key, sizeof(key), "n%u", s); st_->remove(key);
    memset(&e_[s - 1], 0, sizeof(AquaRegEntry)); rev_++; return s;
  }
  // New hardware behind an existing number: keeps slot + number (and therefore the history), pending again.
  int replace(uint8_t type, uint8_t number, const uint8_t mac[6], const char* codeIn, const uint8_t rnd8[8]) {
    int s = findByNumber(type, number); if (s < 0) return AQUA_REG_NOT_FOUND;
    char code[AQUA_CODE_LEN + 1]; if (!aquaCodeNormalize(codeIn ? codeIn : "", code)) return AQUA_REG_BAD_CODE;
    int other = findByMac(mac); if (other > 0 && other != s) return AQUA_REG_DUP_MAC;
    AquaRegEntry& x = e_[s - 1];
    memcpy(x.mac, mac, 6); memcpy(x.code, code, AQUA_CODE_LEN + 1); memcpy(x.salt, rnd8, 8); x.state = 1;
    save(s); rev_++; return s;
  }
  int rename(uint8_t type, uint8_t number, const char* name) {
    int s = findByNumber(type, number); if (s < 0) return AQUA_REG_NOT_FOUND;
    char nm[25]; aquaRegCleanName(name, nm); if (!nm[0]) return AQUA_REG_BAD_ARG;
    memcpy(e_[s - 1].name, nm, sizeof(nm)); save(s); rev_++; return s;
  }
  int setCalibration(uint8_t type, uint8_t number, uint16_t pplX10, uint8_t flags) {
    int s = findByNumber(type, number); if (s < 0) return AQUA_REG_NOT_FOUND;
    if (pplX10 != 0 && (pplX10 < 1000 || pplX10 > 60000)) return AQUA_REG_BAD_ARG;      // 100..6000 pulses/L
    e_[s - 1].pplX10 = pplX10; e_[s - 1].cfgFlags = flags & AQUA_CFG_KNOWN_MASK; save(s); rev_++; return s;
  }
  void markPaired(uint8_t slot) { if (e_[slot - 1].used && e_[slot - 1].state != 2) { e_[slot - 1].state = 2; save(slot); rev_++; } }
  void clearAll() { for (uint8_t s = 1; s <= AQUA_MAX_SLOTS; s++) { char k[8]; snprintf(k, sizeof(k), "n%u", s); st_->remove(k); }
                    memset(e_, 0, sizeof(e_)); st_->remove("nextFlow"); st_->remove("nextRain"); next_[0] = next_[1] = 1; rev_++; }

 private:
  void save(uint8_t slot) {
    const AquaRegEntry& x = e_[slot - 1]; char mac[13], salt[17], line[128], key[8];
    aquaHexEnc(mac, x.mac, 6); aquaHexEnc(salt, x.salt, 8);
    snprintf(line, sizeof(line), "%u|%u|%s|%s|%s|%u|%u|%u|%lu|%s", x.number, x.devType, mac, x.code, salt, x.pplX10, x.cfgFlags,
             x.state, (unsigned long)x.addedEpoch, x.name);
    snprintf(key, sizeof(key), "n%u", slot); st_->putStr(key, line);
  }
  void load(uint8_t slot) {
    char key[8], line[128]; snprintf(key, sizeof(key), "n%u", slot);
    if (!st_->getStr(key, line, sizeof(line))) return;
    char* f[10]; int n = 0; char* p = line; f[n++] = p;
    for (; *p && n < 10; p++) if (*p == '|') { *p = 0; f[n++] = p + 1; }
    if (n < 10) return;
    AquaRegEntry x; memset(&x, 0, sizeof(x));
    x.number = (uint8_t)atoi(f[0]); x.devType = (uint8_t)atoi(f[1]);
    if (!aquaHexDec(f[2], 6, x.mac) || strlen(f[2]) != 12 || !aquaCodeNormalize(f[3], x.code) || strlen(f[4]) != 16 || !aquaHexDec(f[4], 8, x.salt)) return;
    if (x.number == 0 || (x.devType != AQUA_DEV_FLOW && x.devType != AQUA_DEV_RAIN)) return;
    x.pplX10 = (uint16_t)atoi(f[5]); x.cfgFlags = (uint8_t)atoi(f[6]); x.state = (uint8_t)atoi(f[7]); if (x.state != 1 && x.state != 2) x.state = 1;
    x.addedEpoch = (uint32_t)strtoul(f[8], nullptr, 10); aquaRegCleanName(f[9], x.name);
    x.used = true; x.slot = slot; e_[slot - 1] = x;
  }
  Store* st_ = nullptr;
  AquaRegEntry e_[AQUA_MAX_SLOTS];
  uint8_t next_[2] = {1, 1};
  uint32_t rev_ = 0;
};

// ===== signed command AQC2 ====================================================================================
enum AquaCmd2Rc : int {
  AQUA_C2_OK = 0, AQUA_C2_FORMAT = -1, AQUA_C2_TAG = -2, AQUA_C2_NO_TIME = -3, AQUA_C2_STALE = -4, AQUA_C2_REPLAY = -5, AQUA_C2_FIELD = -6
};

struct AquaCmd2 {
  uint32_t ts;
  uint8_t  nonce[12];
  char     op;                       // A N R P C
  uint8_t  devType, number;
  bool     hasMac; uint8_t mac[6];
  bool     hasCal; uint16_t pplX10; uint8_t flags;
  char     name[25];
  bool     hasCode; char code[AQUA_CODE_LEN + 1];   // decrypted
};

// 32-byte ChaCha20 key from the 16-byte site command key.
static inline void aquaCmd2EncKey(const uint8_t siteKey[16], uint8_t out[32]) { aquaKdf(siteKey, 'E', 0, 0, out); aquaKdf(siteKey, 'E', 1, 0, out + 16); }

class AquaCmd2Guard {                                 // replay state of one gateway
 public:
  uint32_t lastTs = 0;
  void remember(uint32_t ts, const uint8_t nonce[12]) { lastTs = ts; memcpy(ring_[pos_], nonce, 12); pos_ = (pos_ + 1) % 8; used_ = used_ < 8 ? used_ + 1 : 8; }
  bool seen(const uint8_t nonce[12]) const { for (uint8_t i = 0; i < used_; i++) if (!memcmp(ring_[i], nonce, 12)) return true; return false; }
 private:
  uint8_t ring_[8][12] = {{0}}; uint8_t pos_ = 0, used_ = 0;
};

static const uint32_t AQUA_CMD2_WINDOW_S = 300;

// Parse + verify + decrypt.  epochNow = 0 means "gateway time not synced" -> fail closed.
static inline int aquaCmd2Parse(const char* line, size_t len, const uint8_t siteKey[16], uint32_t epochNow, AquaCmd2Guard& g, AquaCmd2* out) {
  char buf[256]; if (len == 0 || len >= sizeof(buf)) return AQUA_C2_FORMAT;
  memcpy(buf, line, len); buf[len] = 0;
  while (len && (buf[len - 1] == '\n' || buf[len - 1] == '\r' || buf[len - 1] == ' ')) buf[--len] = 0;
  char* f[13]; int n = 0; char* p = buf; f[n++] = p;
  for (; *p; p++) if (*p == ' ') { if (n >= 13) return AQUA_C2_FORMAT; *p = 0; f[n++] = p + 1; }
  if (n != 12 || strcmp(f[0], "AQC2") != 0) return AQUA_C2_FORMAT;
  // tag covers everything before " <tag>"
  size_t bodyLen = (size_t)(f[11] - buf) - 1; uint8_t cmdKey[16]; aquaCmdKey(siteKey, cmdKey);
  char want[17], bodyCopy[256]; memcpy(bodyCopy, line, bodyLen);                       // original text (separators intact)
  aquaTextTag(cmdKey, bodyCopy, bodyLen, want);
  if (strlen(f[11]) != 16) return AQUA_C2_FORMAT;
  uint8_t diff = 0; for (int i = 0; i < 16; i++) diff |= (uint8_t)(f[11][i] ^ want[i]);   // constant time (lower-case hex both)
  if (diff) return AQUA_C2_TAG;
  if (epochNow == 0) return AQUA_C2_NO_TIME;
  AquaCmd2 c; memset(&c, 0, sizeof(c));
  c.ts = (uint32_t)strtoul(f[1], nullptr, 10);
  uint32_t d = c.ts > epochNow ? c.ts - epochNow : epochNow - c.ts;
  if (d > AQUA_CMD2_WINDOW_S || c.ts < g.lastTs) return AQUA_C2_STALE;
  if (strlen(f[2]) != 24 || !aquaHexDec(f[2], 12, c.nonce)) return AQUA_C2_FORMAT;
  if (g.seen(c.nonce)) return AQUA_C2_REPLAY;
  if (strlen(f[3]) != 1 || !strchr("ANRPC", f[3][0])) return AQUA_C2_FIELD;
  c.op = f[3][0]; c.devType = (uint8_t)atoi(f[4]); c.number = (uint8_t)atoi(f[5]);
  if (c.devType != AQUA_DEV_FLOW && c.devType != AQUA_DEV_RAIN) return AQUA_C2_FIELD;
  if (strcmp(f[6], "-") != 0) { if (strlen(f[6]) != 12 || !aquaHexDec(f[6], 6, c.mac)) return AQUA_C2_FIELD; c.hasMac = true; }
  if (strcmp(f[7], "-") != 0) { c.hasCal = true; c.pplX10 = (uint16_t)atoi(f[7]); c.flags = (uint8_t)atoi(f[8]); }
  if (strcmp(f[9], "-") != 0) {
    size_t hl = strlen(f[9]); uint8_t nb[24]; if (hl > 48 || (hl & 1) || !aquaHexDec(f[9], hl / 2, nb)) return AQUA_C2_FIELD;
    char tmp[25]; memcpy(tmp, nb, hl / 2); tmp[hl / 2] = 0; aquaRegCleanName(tmp, c.name);
  }
  if (strcmp(f[10], "-") != 0) {
    uint8_t enc[AQUA_CODE_LEN], key[32]; if (strlen(f[10]) != 2 * AQUA_CODE_LEN || !aquaHexDec(f[10], AQUA_CODE_LEN, enc)) return AQUA_C2_FIELD;
    aquaCmd2EncKey(siteKey, key); aquaChaChaXor(key, c.nonce, 0, enc, AQUA_CODE_LEN);
    char raw[AQUA_CODE_LEN + 1]; memcpy(raw, enc, AQUA_CODE_LEN); raw[AQUA_CODE_LEN] = 0;
    if (!aquaCodeNormalize(raw, c.code)) return AQUA_C2_FIELD;
    c.hasCode = true;
  }
  // per-op required fields
  if ((c.op == 'A' && (!c.hasMac || !c.hasCode)) || (c.op == 'P' && (!c.hasMac || !c.hasCode || !c.number)) ||
      ((c.op == 'N' || c.op == 'R' || c.op == 'C') && !c.number) || (c.op == 'N' && !c.name[0]) || (c.op == 'C' && !c.hasCal)) return AQUA_C2_FIELD;
  g.remember(c.ts, c.nonce);
  *out = c; return AQUA_C2_OK;
}

// Build a command line (used by the host tests and by the Python-compat vectors; the backend has its own implementation).
static inline int aquaCmd2Build(char* out, size_t cap, const uint8_t siteKey[16], uint32_t ts, const uint8_t nonce[12], char op, uint8_t type,
                                uint8_t number, const uint8_t* mac, int ppl, int flags, const char* name, const char* code) {
  char nh[25], mh[13], ch[25], nn[49] = "-";
  aquaHexEnc(nh, nonce, 12);
  if (mac) aquaHexEnc(mh, mac, 6); else strcpy(mh, "-");
  if (name && *name) aquaHexEnc(nn, (const uint8_t*)name, strlen(name));
  if (code) { uint8_t enc[AQUA_CODE_LEN], key[32]; memcpy(enc, code, AQUA_CODE_LEN); aquaCmd2EncKey(siteKey, key); aquaChaChaXor(key, nonce, 0, enc, AQUA_CODE_LEN); aquaHexEnc(ch, enc, AQUA_CODE_LEN); }
  else strcpy(ch, "-");
  char cal[24] = "- -"; if (ppl >= 0) snprintf(cal, sizeof(cal), "%d %d", ppl, flags);
  int n = snprintf(out, cap, "AQC2 %lu %s %c %u %u %s %s %s %s", (unsigned long)ts, nh, op, type, number, mh, cal, nn, ch);
  if (n <= 0 || (size_t)n + 18 > cap) return -1;
  uint8_t ck[16]; aquaCmdKey(siteKey, ck); char tag[17]; aquaTextTag(ck, out, n, tag);
  return n + snprintf(out + n, cap - n, " %s", tag);
}

// Apply a verified command to the registry.  Returns the AquaRegRc (slot > 0 on success).
template <class Store>
static inline int aquaCmd2Apply(AquaRegistry<Store>& reg, const AquaCmd2& c, uint32_t epoch, const uint8_t rnd8[8]) {
  switch (c.op) {
    case 'A': return reg.add(c.devType, c.mac, c.code, c.name, epoch, rnd8);
    case 'P': return reg.replace(c.devType, c.number, c.mac, c.code, rnd8);
    case 'N': return reg.rename(c.devType, c.number, c.name);
    case 'R': return reg.remove(c.devType, c.number);
    case 'C': return reg.setCalibration(c.devType, c.number, c.pplX10, c.flags);
  }
  return AQUA_REG_BAD_ARG;
}

// Make the protocol core follow the registry (NVS decides, the core follows).  send(out) transmits an UNPAIR frame
// when a removed node still has a live session.  Safe to call repeatedly.
template <class Store, class SendFn>
static inline void aquaRegSyncCore(AquaRegistry<Store>& reg, AquaGatewayCore& core, SendFn send) {
  for (uint8_t s = 1; s <= AQUA_MAX_SLOTS; s++) {
    const AquaRegEntry& e = reg.at(s); AquaGatewayCore::Node& n = core.node(s);
    AquaGwOut out; uint8_t kp[16];
    if (!e.used) { if (n.state != AquaGatewayCore::NS_FREE && core.removeNode(s, &out)) send(out); continue; }
    aquaPairingKey(e.code, e.mac, kp);
    if (n.state == AquaGatewayCore::NS_FREE) core.registerNode(s, e.number, e.devType, e.mac, kp, e.salt, e.state == 2);
    else if (memcmp(n.mac, e.mac, 6) != 0) {
      if (n.number != e.number || n.devType != e.devType) {            // a different node took the slot: start clean
        if (core.removeNode(s, &out)) send(out);
        core.registerNode(s, e.number, e.devType, e.mac, kp, e.salt, e.state == 2);
      } else core.replaceNode(s, e.mac, kp, e.salt);                    // new hardware behind the same number
    }
    core.setNodeConfig(s, e.pplX10, e.cfgFlags);
  }
}
