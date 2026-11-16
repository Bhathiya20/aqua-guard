/*
 * Gateway-side per-node sequence tracker (pure logic, host-testable).
 *
 * Tracks one boot session per node:
 *   cumAck  = highest contiguous sequence number received
 *   window  = 32-bit bitmap, bit i <=> seq (cumAck + 1 + i) received
 * Detects duplicates, out-of-order arrival, gaps, recoveries and reboots, and
 * decides when to ask for retransmission (NACK) or give up on a sequence.
 *
 * "lost" means the gateway gave up on a sequence.  It does NOT mean water
 * volume was lost: volume is reconciled from cumulative pulse counters.
 */
#pragma once
#include <stdint.h>
#include "aqua_protocol.h"

#ifndef AQUA_NACK_DELAY_MS
#define AQUA_NACK_DELAY_MS   300   // wait for natural reordering before NACKing
#endif
#ifndef AQUA_NACK_REPEAT_MS
#define AQUA_NACK_REPEAT_MS  800
#endif
#ifndef AQUA_NACK_ROUNDS
#define AQUA_NACK_ROUNDS     4     // NACK attempts before declaring a sequence lost
#endif
#define AQUA_PREV_BOOTS      4

enum AquaRxResult : uint8_t {
  AQUA_RX_NEW_IN_ORDER = 0,   // next expected sequence
  AQUA_RX_NEW_AHEAD,          // new, but a gap precedes it
  AQUA_RX_RECOVERED,          // fills a previously detected gap
  AQUA_RX_DUPLICATE,
  AQUA_RX_STALE_SESSION       // frame from an older boot session (ignored)
};

struct AquaRxTracker {
  bool     active;
  uint32_t bootId;
  uint32_t cumAck;
  uint32_t window;
  uint32_t highest;
  uint32_t prevBoots[AQUA_PREV_BOOTS];
  uint8_t  prevBootIdx;
  // statistics
  uint32_t received, duplicates, recovered, lost, outOfOrder, sessions, nacksSent, staleSession;
  // NACK scheduling
  uint32_t gapSinceMs, lastNackMs;
  uint8_t  nackRounds;

  void reset() { memset(this, 0, sizeof(*this)); }

  static int popcountZeroBits(uint32_t w, uint32_t n) {   // zero bits among lowest n bits
    int z = 0; for (uint32_t i = 0; i < n && i < 32; i++) if (!((w >> i) & 1u)) z++;
    return z;
  }

  bool knownOldBoot(uint32_t b) const {
    for (uint8_t i = 0; i < AQUA_PREV_BOOTS; i++) if (prevBoots[i] == b && b != 0) return true;
    return false;
  }

  // Advance cumAck over contiguous received bits.
  void advance() {
    while (window & 1u) { window >>= 1; cumAck++; }
  }

  // Force the window forward so that (seq - cumAck) <= 32; sequences dropped
  // from the window that were never received are counted lost.
  void slideFor(uint32_t seq) {
    uint32_t d = seq - cumAck;
    if (d <= 32) return;
    uint32_t shift = d - 32;
    uint32_t inWindow = shift < 32 ? shift : 32;
    lost += popcountZeroBits(window, inWindow);
    if (shift > 32) lost += (shift - 32);
    window = shift >= 32 ? 0u : (window >> shift);
    cumAck += shift;
    advance();
  }

  // Register a received DATA frame.
  AquaRxResult onData(uint32_t boot, uint32_t seq, bool* newSession) {
    *newSession = false;
    if (!active || boot != bootId) {
      if (active && knownOldBoot(boot)) { staleSession++; return AQUA_RX_STALE_SESSION; }
      if (active) { prevBoots[prevBootIdx++ % AQUA_PREV_BOOTS] = bootId; }
      active = true; bootId = boot; sessions++;
      cumAck = seq - 1;       // baseline at first seen frame: earlier ones are not chased
      window = 0; highest = seq - 1;
      gapSinceMs = 0; nackRounds = 0;
      *newSession = true;
    }
    if ((int32_t)(seq - cumAck) <= 0) { duplicates++; return AQUA_RX_DUPLICATE; }
    slideFor(seq);
    uint32_t d = seq - cumAck;             // 1..32
    if ((window >> (d - 1)) & 1u) { duplicates++; return AQUA_RX_DUPLICATE; }
    received++;
    AquaRxResult res;
    if ((int32_t)(seq - highest) > 0) {
      res = (seq == highest + 1) ? AQUA_RX_NEW_IN_ORDER : AQUA_RX_NEW_AHEAD;
      if (res == AQUA_RX_NEW_AHEAD) outOfOrder++;
      highest = seq;
    } else {
      res = AQUA_RX_RECOVERED; recovered++;
    }
    window |= (1u << (d - 1));
    advance();
    if (missingCount() == 0) { gapSinceMs = 0; nackRounds = 0; }
    return res;
  }

  // Heartbeat reports the node's latest DATA seq: detects trailing loss.
  void onHeartbeat(uint32_t boot, uint32_t lastDataSeq) {
    if (!active || boot != bootId || (int32_t)(lastDataSeq - highest) <= 0) return;
    slideFor(lastDataSeq);
    highest = lastDataSeq;
  }

  uint32_t missingCount() const {
    uint32_t span = highest - cumAck; if (span > 32) span = 32;
    uint32_t n = 0;
    for (uint32_t i = 0; i < span; i++) if (!((window >> i) & 1u)) n++;
    return n;
  }

  uint32_t missingList(uint32_t* out, uint32_t max) const {
    uint32_t span = highest - cumAck; if (span > 32) span = 32;
    uint32_t n = 0;
    for (uint32_t i = 0; i < span && n < max; i++)
      if (!((window >> i) & 1u)) out[n++] = cumAck + 1 + i;
    return n;
  }

  // Periodic: returns true and fills *nack when a NACK should be sent now.
  // After AQUA_NACK_ROUNDS unanswered rounds the oldest missing seq is
  // abandoned (counted lost) so the window never stalls.
  bool poll(uint32_t nowMs, AquaNack* nack) {
    if (!active) return false;
    uint32_t miss[AQUA_NACK_MAX];
    uint32_t n = missingList(miss, AQUA_NACK_MAX);
    if (n == 0) { gapSinceMs = 0; nackRounds = 0; return false; }
    if (gapSinceMs == 0) { gapSinceMs = nowMs ? nowMs : 1; return false; }
    if ((uint32_t)(nowMs - gapSinceMs) < AQUA_NACK_DELAY_MS) return false;
    if (nackRounds > 0 && (uint32_t)(nowMs - lastNackMs) < AQUA_NACK_REPEAT_MS) return false;
    if (nackRounds >= AQUA_NACK_ROUNDS) {
      // give up on the oldest missing sequence: mark it lost, move on
      window |= (1u << (miss[0] - cumAck - 1));
      lost++;
      advance();
      nackRounds = 0; gapSinceMs = nowMs ? nowMs : 1;
      return false;
    }
    memset(nack, 0, sizeof(*nack));
    nack->count = (uint8_t)n;
    for (uint32_t i = 0; i < n; i++) nack->seqs[i] = miss[i];
    nackRounds++; lastNackMs = nowMs; nacksSent++;
    return true;
  }
};
