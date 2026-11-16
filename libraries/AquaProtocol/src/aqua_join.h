/*
 * Pairing identity helpers (pure C++, host-testable, no Arduino dependency):
 *   - pairing code: 12 symbols of an unambiguous 32-symbol alphabet (60 bits), shown as XXXX-XXXX-XXXX
 *   - pairing key Kp derived from code + MAC, node key Kn, join MACs
 *   - QR payload build/parse (URL form and compact form) - the same rules are implemented in
 *     tools/aqua_qr.py, the gateway portal JS and the web app (tests/qr_vectors.json holds the shared cases)
 *
 * Why 12 symbols: the handshake frames are visible on the air, so an 8-symbol code (40 bits) could be
 * brute-forced offline from ONE captured handshake. 60 bits make that impractical.
 */
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include "aqua_protocol.h"

#define AQUA_CODE_LEN 12

// Crockford base32: no I, L, O, U.  Reading rules: O->0, I/L->1 (a person typing from a sticker).
static const char AQUA_CODE_ALPHABET[33] = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

static inline int aquaCodeSymbolValue(char c) {
  if (c >= 'a' && c <= 'z') c = (char)(c - 'a' + 'A');
  if (c == 'O') c = '0';
  if (c == 'I' || c == 'L') c = '1';
  for (int i = 0; i < 32; i++) if (AQUA_CODE_ALPHABET[i] == c) return i;
  return -1;
}

// Normalise user input: ignore dashes/spaces/case, apply the reading rules.  out = 12 symbols + NUL.
static inline bool aquaCodeNormalize(const char* in, char out[AQUA_CODE_LEN + 1]) {
  int n = 0;
  for (; *in; in++) {
    char c = *in;
    if (c == '-' || c == ' ' || c == '_') continue;
    int v = aquaCodeSymbolValue(c);
    if (v < 0 || n >= AQUA_CODE_LEN) return false;
    out[n++] = AQUA_CODE_ALPHABET[v];
  }
  if (n != AQUA_CODE_LEN) return false;
  out[n] = 0;
  return true;
}

// rnd: 8 random bytes (only 60 bits are used).
static inline void aquaCodeFromRandom(const uint8_t rnd[8], char out[AQUA_CODE_LEN + 1]) {
  uint64_t v = 0; memcpy(&v, rnd, 8);
  for (int i = 0; i < AQUA_CODE_LEN; i++) { out[i] = AQUA_CODE_ALPHABET[v & 31]; v >>= 5; }
  out[AQUA_CODE_LEN] = 0;
}

// "XXXX-XXXX-XXXX" (out needs 15 bytes).
static inline void aquaCodeFormat(const char code[AQUA_CODE_LEN + 1], char out[15]) {
  int o = 0;
  for (int i = 0; i < AQUA_CODE_LEN; i++) { if (i && i % 4 == 0) out[o++] = '-'; out[o++] = code[i]; }
  out[o] = 0;
}

static inline void aquaMacFormat(const uint8_t mac[6], char out[18]) {
  static const char* H = "0123456789ABCDEF";
  int o = 0;
  for (int i = 0; i < 6; i++) { if (i) out[o++] = ':'; out[o++] = H[mac[i] >> 4]; out[o++] = H[mac[i] & 15]; }
  out[o] = 0;
}

// Accepts AA:BB:CC:DD:EE:FF, AA-BB-..., AABBCCDDEEFF, any case.
static inline bool aquaMacParse(const char* s, uint8_t mac[6]) {
  int n = 0, hi = -1;
  for (; *s; s++) {
    char c = *s; int v;
    if (c == ':' || c == '-' || c == ' ') continue;
    if (c >= '0' && c <= '9') v = c - '0'; else if (c >= 'a' && c <= 'f') v = c - 'a' + 10;
    else if (c >= 'A' && c <= 'F') v = c - 'A' + 10; else return false;
    if (hi < 0) hi = v; else { if (n >= 6) return false; mac[n++] = (uint8_t)((hi << 4) | v); hi = -1; }
  }
  return n == 6 && hi < 0;
}

// A key that is all-zero / one repeated byte is a template or a mistake, never a real site key.
static inline bool aquaKeyIsWeak(const uint8_t k[16]) {
  for (int i = 1; i < 16; i++) if (k[i] != k[0]) return false;
  return true;
}

// ---- keys ------------------------------------------------------------------------------
// Kp = f(code, mac).  The code is the secret; the MAC is mixed in so two nodes never share a key even if a
// code were ever reused.  (Not a password-hardening KDF: the 60-bit random code is what protects it.)
static inline void aquaPairingKey(const char code[AQUA_CODE_LEN + 1], const uint8_t mac[6], uint8_t out[16]) {
  static const uint8_t DOM[16] = {'A','q','u','a','G','u','a','r','d','-','K','p','-','v','2',0};
  uint8_t in[4 + AQUA_CODE_LEN + 6 + 1];
  memcpy(in, "AQP2", 4); memcpy(in + 4, code, AQUA_CODE_LEN); memcpy(in + 4 + AQUA_CODE_LEN, mac, 6);
  in[sizeof(in) - 1] = 0; uint64_t h0 = aquaSipHash24(DOM, in, sizeof(in));
  in[sizeof(in) - 1] = 1; uint64_t h1 = aquaSipHash24(DOM, in, sizeof(in));
  memcpy(out, &h0, 8); memcpy(out + 8, &h1, 8);
}

static inline void aquaNodeKey(const uint8_t kp[16], const uint8_t salt[8], uint8_t out[16]) {
  uint32_t a, b; memcpy(&a, salt, 4); memcpy(&b, salt + 4, 4);
  aquaKdf(kp, 'N', a, b, out);
}
static inline void aquaSessionKey(const uint8_t kn[16], uint32_t bootId, uint8_t out[16]) {
  aquaKdf(kn, 'K', 0, bootId, out);
}

// Handshake MAC: label 'R' (node response; slot=0, salt=zeros) or 'A' (gateway accept proof).
static inline void aquaJoinMac(const uint8_t kp[16], char label, const uint8_t mac[6], uint32_t bootId,
                               const uint8_t chal[8], uint8_t slot, const uint8_t salt[8], uint8_t out[8]) {
  uint8_t in[1 + 6 + 4 + 8 + 1 + 8];
  in[0] = (uint8_t)label; memcpy(in + 1, mac, 6); memcpy(in + 7, &bootId, 4); memcpy(in + 11, chal, 8);
  in[19] = slot; memcpy(in + 20, salt, 8);
  uint64_t m = aquaSipHash24(kp, in, sizeof(in));
  memcpy(out, &m, 8);
}

// ---- QR payload ----------------------------------------------------------------------------
//   URL     : http://192.168.4.1/add?v=1&mac=AA:BB:CC:DD:EE:FF&code=XXXXXXXXXXXX&type=flow
//   compact : AQG1;AABBCCDDEEFF;XXXXXXXXXXXX;flow
struct AquaQr { uint8_t mac[6]; char code[AQUA_CODE_LEN + 1]; uint8_t type; };   // type = AquaDeviceType
enum AquaQrRc : int8_t { AQUA_QR_OK = 0, AQUA_QR_FORMAT = -1, AQUA_QR_VERSION = -2, AQUA_QR_MAC = -3, AQUA_QR_CODE = -4, AQUA_QR_TYPE = -5 };

static inline const char* aquaTypeName(uint8_t t) { return t == AQUA_DEV_RAIN ? "rain" : "flow"; }

static inline bool aquaEqNoCase(const char* a, size_t n, const char* lit) {
  if (n != strlen(lit)) return false;
  for (size_t i = 0; i < n; i++) {
    char x = a[i], y = lit[i];
    if (x >= 'A' && x <= 'Z') x = (char)(x + 32);
    if (y >= 'A' && y <= 'Z') y = (char)(y + 32);
    if (x != y) return false;
  }
  return true;
}
static inline bool aquaTypeParse(const char* s, size_t n, uint8_t* t) {
  if (aquaEqNoCase(s, n, "flow")) { *t = AQUA_DEV_FLOW; return true; }
  if (aquaEqNoCase(s, n, "rain")) { *t = AQUA_DEV_RAIN; return true; }
  return false;
}

// In-place %XX decoding (only what a MAC with %3A needs, but generic).
static inline void aquaUrlDecode(char* s) {
  char* o = s;
  for (; *s; s++) {
    if (*s == '%' && s[1] && s[2]) {
      int a = -1, b = -1;
      char c1 = s[1], c2 = s[2];
      a = (c1 >= '0' && c1 <= '9') ? c1 - '0' : (c1 >= 'a' && c1 <= 'f') ? c1 - 'a' + 10 : (c1 >= 'A' && c1 <= 'F') ? c1 - 'A' + 10 : -1;
      b = (c2 >= '0' && c2 <= '9') ? c2 - '0' : (c2 >= 'a' && c2 <= 'f') ? c2 - 'a' + 10 : (c2 >= 'A' && c2 <= 'F') ? c2 - 'A' + 10 : -1;
      if (a >= 0 && b >= 0) { *o++ = (char)((a << 4) | b); s += 2; continue; }
    }
    *o++ = *s;
  }
  *o = 0;
}

// Error precedence: format -> version -> mac -> code -> type.
static inline int aquaQrParse(const char* text, AquaQr* q) {
  char buf[200];
  size_t n = text ? strlen(text) : 0;
  while (n && (text[0] == ' ' || text[0] == '\t' || text[0] == '\r' || text[0] == '\n')) { text++; n--; }
  while (n && (text[n - 1] == ' ' || text[n - 1] == '\t' || text[n - 1] == '\r' || text[n - 1] == '\n')) n--;
  if (n == 0 || n >= sizeof(buf)) return AQUA_QR_FORMAT;
  memcpy(buf, text, n); buf[n] = 0;

  const char *mac = nullptr, *code = nullptr, *type = nullptr, *ver = nullptr;
  char* fields[4] = {nullptr, nullptr, nullptr, nullptr};
  char* kv[8]; int nkv = 0;

  if (n >= 3 && aquaEqNoCase(buf, 3, "AQG")) {                          // compact form
    int nf = 0; char* p = buf; fields[nf++] = p;
    for (; *p; p++) if (*p == ';') { *p = 0; if (nf >= 4) return AQUA_QR_FORMAT; fields[nf++] = p + 1; }
    if (nf != 4) return AQUA_QR_FORMAT;
    if (!aquaEqNoCase(fields[0], strlen(fields[0]), "AQG1")) return AQUA_QR_VERSION;
    ver = "1"; mac = fields[1]; code = fields[2]; type = fields[3];
  } else {                                                               // URL form
    char* qm = strchr(buf, '?');
    if (!qm || !strstr(buf, "/add")) return AQUA_QR_FORMAT;
    char* p = qm + 1;
    while (*p && nkv < 8) {
      kv[nkv++] = p;
      char* amp = strchr(p, '&');
      if (!amp) break;
      *amp = 0; p = amp + 1;
    }
    for (int i = 0; i < nkv; i++) {
      char* eq = strchr(kv[i], '=');
      if (!eq) continue;
      *eq = 0; char* val = eq + 1; aquaUrlDecode(val);
      if (strcmp(kv[i], "v") == 0) ver = val;
      else if (strcmp(kv[i], "mac") == 0) mac = val;
      else if (strcmp(kv[i], "code") == 0) code = val;
      else if (strcmp(kv[i], "type") == 0) type = val;
    }
    if (!ver || !mac || !code || !type) return AQUA_QR_FORMAT;
    if (strcmp(ver, "1") != 0) return AQUA_QR_VERSION;
  }
  if (!aquaMacParse(mac, q->mac)) return AQUA_QR_MAC;
  if (!aquaCodeNormalize(code, q->code)) return AQUA_QR_CODE;
  if (!aquaTypeParse(type, strlen(type), &q->type)) return AQUA_QR_TYPE;
  return AQUA_QR_OK;
}

// Returns the length written (excluding NUL), or -1 if cap is too small.
static inline int aquaQrBuild(const uint8_t mac[6], const char code[AQUA_CODE_LEN + 1], uint8_t type, bool compact,
                              char* out, size_t cap) {
  char m[18]; aquaMacFormat(mac, m);
  const char* t = aquaTypeName(type);
  size_t need = compact ? (5 + 12 + 1 + AQUA_CODE_LEN + 1 + strlen(t) + 1) : (37 + 18 + 6 + AQUA_CODE_LEN + 6 + strlen(t) + 1);
  if (cap < need) return -1;
  char* o = out;
  if (compact) {
    memcpy(o, "AQG1;", 5); o += 5;
    for (int i = 0; i < 6; i++) { static const char* H = "0123456789ABCDEF"; *o++ = H[mac[i] >> 4]; *o++ = H[mac[i] & 15]; }
    *o++ = ';'; memcpy(o, code, AQUA_CODE_LEN); o += AQUA_CODE_LEN; *o++ = ';';
  } else {
    const char* pre = "http://192.168.4.1/add?v=1&mac="; size_t pl = strlen(pre);
    memcpy(o, pre, pl); o += pl; memcpy(o, m, 17); o += 17;
    memcpy(o, "&code=", 6); o += 6; memcpy(o, code, AQUA_CODE_LEN); o += AQUA_CODE_LEN;
    memcpy(o, "&type=", 6); o += 6;
  }
  size_t tl = strlen(t); memcpy(o, t, tl); o += tl; *o = 0;
  return (int)(o - out);
}
