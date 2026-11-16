/*
 * Gateway -> backend pairing events and shared text helpers (pure C++, host-testable).
 *
 * Authentication: SipHash-2-4 tag keyed by Kcmd = KDF(site command key,'C').  Anybody who can publish on the
 * broker cannot fake an event without the site command key.
 *
 *   gateway -> backend   <prefix>/pairing/event      (envelope unchanged from Milestone 3)
 *     AQE1 <D|O|F> <number> <mac12hex> <devType> <fw> <channel> <rssi> <boot8hex> <slot> - - <epoch> <tag16hex>
 *        D = node nearby, waiting to pair (number 0)     O = handshake OK (number = display number)
 *        F = handshake FAILED (wrong pairing code / clone)
 *     The two "-" fields (challenge / response) are always empty now: the backend no longer re-verifies the
 *     handshake, because it never sees the pairing key.  They are kept so the line format and its parser
 *     stay as they were.
 *
 * The signed command side (backend -> gateway, AQC2: add / rename / remove / replace / calibrate with an
 * AES-CTR encrypted pairing code) is implemented with the gateway registry in the gateway firmware.
 */
#pragma once
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "aqua_protocol.h"
#include "aqua_gateway_core.h"

static inline void aquaHexEnc(char* out, const uint8_t* in, size_t n) {
  static const char* H = "0123456789abcdef";
  for (size_t i = 0; i < n; i++) { out[2 * i] = H[in[i] >> 4]; out[2 * i + 1] = H[in[i] & 15]; }
  out[2 * n] = 0;
}
static inline int aquaHexVal(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}
static inline bool aquaHexDec(const char* s, size_t nBytes, uint8_t* out) {
  for (size_t i = 0; i < nBytes; i++) {
    int a = aquaHexVal(s[2 * i]), b = aquaHexVal(s[2 * i + 1]);
    if (a < 0 || b < 0) return false;
    out[i] = (uint8_t)((a << 4) | b);
  }
  return true;
}

// Kcmd from the 16-byte site command key (AQUA_CMD_KEY in aqua_secrets.h / AQUAGUARD_CMD_KEY in backend/.env).
static inline void aquaCmdKey(const uint8_t siteKey[16], uint8_t out[16]) { aquaKdf(siteKey, 'C', 0, 0, out); }

static inline void aquaTextTag(const uint8_t cmdKey[16], const char* body, size_t n, char outHex[17]) {
  uint64_t t = aquaSipHash24(cmdKey, (const uint8_t*)body, n);
  uint8_t b[8]; memcpy(b, &t, 8); aquaHexEnc(outHex, b, 8);
}

// number: display number of the registered node (0 for "nearby" events).
static inline int aquaPairEventFormat(char* out, size_t cap, const uint8_t cmdKey[16], const AquaPairEvent& e,
                                      uint8_t number, uint32_t epoch) {
  char mac[13], boot[9]; uint8_t b4[4];
  aquaHexEnc(mac, e.mac, 6);
  memcpy(b4, &e.bootId, 4); aquaHexEnc(boot, b4, 4);
  char k = e.kind == AQUA_PE_DISCOVERED ? 'D' : e.kind == AQUA_PE_PAIR_OK ? 'O' : 'F';
  int n = snprintf(out, cap, "AQE1 %c %u %s %u %u %u %d %s %u - - %lu", k, number, mac, e.devType,
                   e.fwVersion, e.channel, (int)e.rssi, boot, e.slot, (unsigned long)epoch);
  if (n <= 0 || (size_t)n + 18 > cap) return -1;
  char tag[17]; aquaTextTag(cmdKey, out, (size_t)n, tag);
  return n + snprintf(out + n, cap - n, " %s", tag);
}
