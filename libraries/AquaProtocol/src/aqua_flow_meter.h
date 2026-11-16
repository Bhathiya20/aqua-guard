/*
 * Flow computation from pulse-counter snapshots (pure logic, host-testable).
 *
 * The ISR only increments a free-running 32-bit counter and never resets it,
 * so no pulse is lost while the snapshot is calculated.  Each interval:
 *   intervalLiters = intervalPulses / pulsesPerLiter
 *   flowRateLpm    = intervalLiters * 60000 / elapsedMs   (real elapsed time)
 *   totalLiters   += intervalLiters
 * Values are transmitted as fixed point (mL, mL/min) plus raw pulse counters;
 * the backend reconciles volume from the cumulative pulse counter.
 */
#pragma once
#include <stdint.h>
#include <string.h>
#include "aqua_protocol.h"

#ifndef AQUA_FLOW_MAX_LPM
#define AQUA_FLOW_MAX_LPM 35.0f     // YF-S201 range is 1..30 L/min
#endif
#ifndef AQUA_FLOW_NOMINAL_MS
#define AQUA_FLOW_NOMINAL_MS 2000UL
#endif

class AquaFlowMeter {
 public:
  void begin(float pulsesPerLiter, uint32_t nowMs, uint32_t pulseCounter) {
    ppl_ = pulsesPerLiter; lastMs_ = nowMs; lastPulses_ = pulseCounter;
    totalMlAcc_ = 0; zeroLowIntervals_ = 0; zeroLowMs_ = 0;
  }

  // pulseCounter = free-running ISR counter, sampled atomically together with nowMs.
  // pinLow = current level of the pulse pin (for stuck-low detection).
  void compute(uint32_t nowMs, uint32_t pulseCounter, bool pinLow, uint32_t uptimeS, AquaFlowPayload* out) {
    uint32_t elapsed = nowMs - lastMs_;
    uint32_t pulses = pulseCounter - lastPulses_;            // wrap-safe (mod 2^32)
    lastMs_ = nowMs; lastPulses_ = pulseCounter;
    if (elapsed == 0) elapsed = 1;

    float liters = (float)pulses / ppl_;
    float lpm = liters * 60000.0f / (float)elapsed;
    // millilitre accumulator in double-free integer form: keep cumulative from pulses directly
    uint64_t cumMl64 = (uint64_t)pulseCounter * 1000ULL * 10ULL / (uint64_t)(ppl_ * 10.0f + 0.5f);

    memset(out, 0, sizeof(*out));
    out->intervalPulses = pulses;
    out->cumPulses = pulseCounter;
    out->flowMlPerMin = (uint32_t)(lpm * 1000.0f + 0.5f);
    out->intervalMl = (uint32_t)(liters * 1000.0f + 0.5f);
    out->totalMl = (uint32_t)cumMl64;
    out->intervalMs = elapsed;
    out->uptimeS = uptimeS;
    out->pulsesPerLiterX10 = (uint16_t)(ppl_ * 10.0f + 0.5f);

    uint16_t st = 0;
    if (lpm > AQUA_FLOW_MAX_LPM) st |= AQUA_FS_OVER_RANGE;
    if (lpm > 4.0f * AQUA_FLOW_MAX_LPM) st |= AQUA_FS_RATE_ANOMALY;
    if (elapsed > AQUA_FLOW_NOMINAL_MS * 3 / 2 || elapsed < AQUA_FLOW_NOMINAL_MS / 2) st |= AQUA_FS_INTERVAL_JIT;
    if (pulses == 0 && pinLow) {
      zeroLowMs_ += elapsed;
      if (zeroLowMs_ > 60000UL) st |= AQUA_FS_STUCK_LOW;
    } else zeroLowMs_ = 0;
    out->status = st;
  }

  float pulsesPerLiter() const { return ppl_; }

 private:
  float ppl_ = 450.0f;
  uint32_t lastMs_ = 0, lastPulses_ = 0, zeroLowMs_ = 0, zeroLowIntervals_ = 0;
  uint64_t totalMlAcc_ = 0;
};
