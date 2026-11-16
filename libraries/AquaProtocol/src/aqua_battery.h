// Battery voltage from an ADC pin behind a resistor divider - and, just as important, deciding when there is NO battery to report.
// A node without a divider has a FLOATING sense pin: it reads anything (0.1 V ... 3 V), drifts and even follows the neighbouring flow pulses.
// Reporting that as a battery voltage is worse than reporting nothing, so every reading is checked and anything doubtful is AQUA_BATT_NONE.
//
// Pure C++ (no Arduino calls): the node sketch collects the ADC samples, this decides.  Host-tested in tests/firmware/test_aqua.cpp.
#pragma once
#include <stdint.h>
#include "aqua_protocol.h"

#ifndef AQUA_VBAT_MIN_MV
#define AQUA_VBAT_MIN_MV      2500   // below this no usable pack exists (an empty 1S cell is ~3.0 V): a floating pin at 0.2..0.8 V lands here
#endif
#ifndef AQUA_VBAT_MAX_MV
#define AQUA_VBAT_MAX_MV      9000   // 2S Li-ion tops out at 8.4 V; a floating pin often idles near the ADC ceiling (~3.0 V pin = ~9.6 V here)
#endif
#ifndef AQUA_VBAT_MAX_SPREAD_MV
#define AQUA_VBAT_MAX_SPREAD_MV 100  // max - min of the samples at the PIN; a real battery is steady, a floating pin wanders / follows pulses
#endif
#ifndef AQUA_VBAT_MIN_PIN_MV
#define AQUA_VBAT_MIN_PIN_MV  50     // essentially 0 V at the pin: divider not connected to a battery
#endif

// pinMv[n]: ADC readings in millivolts at the pin, taken over a few tens of ms.  rTop/rBot: divider resistors in ohm.
inline uint16_t aquaBatteryMv(const uint16_t* pinMv, int n, uint32_t rTop, uint32_t rBot) {
  if (!pinMv || n < 2 || rBot == 0) return AQUA_BATT_NONE;
  uint32_t sum = 0; uint16_t lo = 0xFFFF, hi = 0;
  for (int i = 0; i < n; i++) { sum += pinMv[i]; if (pinMv[i] < lo) lo = pinMv[i]; if (pinMv[i] > hi) hi = pinMv[i]; }
  uint32_t mean = sum / (uint32_t)n;
  if (mean < AQUA_VBAT_MIN_PIN_MV) return AQUA_BATT_NONE;
  if ((uint32_t)(hi - lo) > AQUA_VBAT_MAX_SPREAD_MV) return AQUA_BATT_NONE;           // unstable: floating / coupled
  uint32_t mv = mean * (rTop + rBot) / rBot;
  if (mv < AQUA_VBAT_MIN_MV || mv > AQUA_VBAT_MAX_MV) return AQUA_BATT_NONE;           // implausible for any supported pack
  return (uint16_t)mv;
}
