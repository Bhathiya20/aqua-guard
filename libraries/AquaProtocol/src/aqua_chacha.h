/*
 * ChaCha20 (RFC 8439), pure C++: used to encrypt the pairing-code field of backend -> gateway commands (AQC2).
 * Same primitive and parameters as Python's cryptography ChaCha20 (nonce = 4-byte little-endian block counter
 * + 12-byte nonce; the counter starts at 0), so the backend and the gateway interoperate; tested against the
 * RFC 8439 vector and the backend's own tests.
 */
#pragma once
#include <stdint.h>
#include <string.h>

static inline uint32_t aquaRotl32(uint32_t v, int n) { return (v << n) | (v >> (32 - n)); }

#define AQUA_CC_QR(a, b, c, d) \
  a += b; d ^= a; d = aquaRotl32(d, 16); c += d; b ^= c; b = aquaRotl32(b, 12); \
  a += b; d ^= a; d = aquaRotl32(d, 8);  c += d; b ^= c; b = aquaRotl32(b, 7);

static inline uint32_t aquaLe32(const uint8_t* p) { return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24); }

// One 64-byte keystream block.
static inline void aquaChaChaBlock(const uint8_t key[32], uint32_t counter, const uint8_t nonce[12], uint8_t out[64]) {
  uint32_t s[16] = {0x61707865u, 0x3320646eu, 0x79622d32u, 0x6b206574u};
  for (int i = 0; i < 8; i++) s[4 + i] = aquaLe32(key + 4 * i);
  s[12] = counter;
  for (int i = 0; i < 3; i++) s[13 + i] = aquaLe32(nonce + 4 * i);
  uint32_t x[16]; memcpy(x, s, sizeof(x));
  for (int r = 0; r < 10; r++) {
    AQUA_CC_QR(x[0], x[4], x[8], x[12]) AQUA_CC_QR(x[1], x[5], x[9], x[13])
    AQUA_CC_QR(x[2], x[6], x[10], x[14]) AQUA_CC_QR(x[3], x[7], x[11], x[15])
    AQUA_CC_QR(x[0], x[5], x[10], x[15]) AQUA_CC_QR(x[1], x[6], x[11], x[12])
    AQUA_CC_QR(x[2], x[7], x[8], x[13]) AQUA_CC_QR(x[3], x[4], x[9], x[14])
  }
  for (int i = 0; i < 16; i++) { uint32_t v = x[i] + s[i]; out[4 * i] = v; out[4 * i + 1] = v >> 8; out[4 * i + 2] = v >> 16; out[4 * i + 3] = v >> 24; }
}

// XOR keystream into data (encrypt == decrypt).  counter0 is the initial block counter (0 for AQC2).
static inline void aquaChaChaXor(const uint8_t key[32], const uint8_t nonce[12], uint32_t counter0, uint8_t* data, size_t len) {
  uint8_t ks[64];
  for (size_t off = 0; off < len; off += 64, counter0++) {
    aquaChaChaBlock(key, counter0, nonce, ks);
    size_t n = len - off < 64 ? len - off : 64;
    for (size_t i = 0; i < n; i++) data[off + i] ^= ks[i];
  }
}
