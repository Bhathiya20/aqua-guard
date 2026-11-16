/*
 * Node-side bounded transmit window / history (pure logic, host-testable).
 *
 * Holds frames that have been produced but not yet acknowledged by the
 * gateway.  Fixed memory: DEPTH slots of MAXFRAME bytes.  Never blocks.
 * Frames are retransmitted with growing timeouts up to AQUA_MAX_ATTEMPTS;
 * after that they are abandoned (counted in `dropped`).  Abandoned frames do
 * not lose water volume, because every later frame carries the cumulative
 * pulse counter.
 */
#pragma once
#include <stdint.h>
#include <string.h>
#include "aqua_protocol.h"

#ifndef AQUA_MAX_ATTEMPTS
#define AQUA_MAX_ATTEMPTS 5
#endif

template <uint8_t DEPTH, uint8_t MAXFRAME>
struct AquaTxWindow {
  struct Slot {
    bool     used;
    bool     forceResend;
    uint8_t  attempts;      // transmissions so far
    uint8_t  len;
    uint32_t seq;
    uint32_t lastTxMs;
    uint8_t  frame[MAXFRAME];
  };
  Slot slots[DEPTH];
  uint32_t evicted, dropped, acked;

  void reset() { memset(this, 0, sizeof(*this)); }

  uint8_t pending() const { uint8_t n = 0; for (uint8_t i = 0; i < DEPTH; i++) n += slots[i].used; return n; }

  // Store a new frame.  If full, the OLDEST pending frame is evicted.
  bool push(uint32_t seq, const uint8_t* frame, uint8_t len) {
    if (len > MAXFRAME) return false;
    int idx = -1;
    for (uint8_t i = 0; i < DEPTH; i++) if (!slots[i].used) { idx = i; break; }
    if (idx < 0) {
      uint8_t oldest = 0;
      for (uint8_t i = 1; i < DEPTH; i++) if ((int32_t)(slots[i].seq - slots[oldest].seq) < 0) oldest = i;
      idx = oldest; evicted++; dropped++;
    }
    Slot& s = slots[idx];
    s.used = true; s.forceResend = false; s.attempts = 0; s.len = len; s.seq = seq; s.lastTxMs = 0;
    memcpy(s.frame, frame, len);
    return true;
  }

  // Apply a gateway ACK: free everything <= cumAck and selectively acked seqs.
  void onAck(uint32_t cumAck, uint32_t window) {
    for (uint8_t i = 0; i < DEPTH; i++) {
      Slot& s = slots[i];
      if (!s.used) continue;
      bool done = (int32_t)(s.seq - cumAck) <= 0;
      if (!done) {
        uint32_t d = s.seq - cumAck;                       // >= 1
        if (d <= 32 && ((window >> (d - 1)) & 1u)) done = true;
      }
      if (done) { s.used = false; acked++; }
    }
  }

  // Gateway asked for this sequence again.
  void requestResend(uint32_t seq) {
    for (uint8_t i = 0; i < DEPTH; i++) if (slots[i].used && slots[i].seq == seq) slots[i].forceResend = true;
  }

  static uint32_t timeoutFor(uint8_t attempts) {           // after N transmissions
    static const uint16_t t[] = {500, 1000, 2000, 2000, 2000, 2000};
    return t[attempts < 6 ? attempts : 5];
  }

  // Choose one frame to transmit now (never-sent first, then resends by age).
  // Returns slot index or -1.  Slots whose attempts are exhausted are dropped.
  int next(uint32_t nowMs) {
    int best = -1;
    for (uint8_t i = 0; i < DEPTH; i++) {
      Slot& s = slots[i];
      if (!s.used) continue;
      bool due;
      if (s.attempts == 0) due = true;
      else if (s.forceResend) due = true;
      else {
        if (s.attempts >= AQUA_MAX_ATTEMPTS && (uint32_t)(nowMs - s.lastTxMs) >= timeoutFor(s.attempts)) {
          s.used = false; dropped++; continue;             // abandon: volume still reconciled by cumulative counter
        }
        due = (uint32_t)(nowMs - s.lastTxMs) >= timeoutFor(s.attempts);
      }
      if (!due) continue;
      if (best < 0 || (int32_t)(s.seq - slots[best].seq) < 0) best = i;
    }
    return best;
  }

  void markSent(int idx, uint32_t nowMs) {
    Slot& s = slots[idx];
    if (s.attempts < 255) s.attempts++;
    s.lastTxMs = nowMs ? nowMs : 1; s.forceResend = false;
  }

  // After re-discovery: everything pending becomes immediately sendable again.
  void rearm() { for (uint8_t i = 0; i < DEPTH; i++) if (slots[i].used) { slots[i].attempts = 0; } }
};
