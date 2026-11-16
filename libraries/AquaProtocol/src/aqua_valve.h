/*
 * Rainwater valve interlock state machine + non-blocking buzzer scheduler.
 * Pure logic (host-testable).  The valve is NEVER opened just because rain is
 * detected.  Opening requires ALL of:
 *   - rain confirmed (debounced),   - first-flush diversion finished,
 *   - tank level VALID and below the tank-full threshold (with hysteresis),
 *   - turbidity VALID and water clear for a stable period (with hysteresis),
 *   - no sensor fault, and minimum time since the last close.
 * Closing for safety (fault, tank full, unsafe water) is immediate.
 *
 * Sensor timeouts are inputs flagged "invalid"; they are never converted to
 * a valid 0 % level.
 */
#pragma once
#include <stdint.h>
#include "aqua_protocol.h"

enum AquaValveReason : uint8_t {
  AQUA_VR_NONE = 0, AQUA_VR_RAIN_NOT_CONFIRMED, AQUA_VR_FIRST_FLUSH, AQUA_VR_WAIT_CLEAR,
  AQUA_VR_COLLECTING, AQUA_VR_TANK_FULL, AQUA_VR_UNSAFE_WATER, AQUA_VR_SENSOR_FAULT,
  AQUA_VR_RAIN_ENDED, AQUA_VR_MIN_INTERVAL, AQUA_VR_TANK_INVALID
};

enum AquaAlarm : uint8_t { AQUA_ALARM_NONE = 0, AQUA_ALARM_TANK_FULL, AQUA_ALARM_UNSAFE_WATER,
                           AQUA_ALARM_SENSOR_FAULT, AQUA_ALARM_LINK_LOST };

struct AquaValveConfig {
  uint32_t rainOnDebounceMs   = 10000;
  uint32_t rainOffDebounceMs  = 60000;
  uint32_t firstFlushMs       = 120000;   // diversion time at the start of each rain event
  uint32_t clearStableMs      = 10000;    // water must stay clear this long before opening
  uint32_t unsafeConfirmMs    = 5000;     // turbid this long => close
  uint32_t sensorTimeoutMs    = 15000;    // invalid this long => sensor fault
  uint32_t faultRecoverMs     = 5000;     // valid this long => fault cleared
  uint32_t minSwitchMs        = 30000;    // minimum closed time before re-opening / hold before normal close
  float    tankFullOnPct      = 95.0f;
  float    tankFullOffPct     = 90.0f;    // re-open allowed only below this
  uint16_t clearOnAdc         = 2600;     // ADC >= => clear   (existing project threshold was 2500)
  uint16_t unsafeBelowAdc     = 2300;     // ADC <= => unsafe
  bool     failSafeOpen       = false;    // valve state commanded on FAULT_SAFE (false = closed)
};

struct AquaValveInputs {
  bool     rainRaw;
  bool     tankValid;
  float    levelPct;
  bool     turbValid;
  uint16_t turbAdc;
};

struct AquaValveOutput {
  AquaValveState  state;
  AquaValveReason reason;
  bool     valveOpen;       // commanded state (before relay polarity / dry-run)
  bool     rainActive;
  bool     firstFlush;
  bool     tankFull;
  bool     waterClear;
  uint16_t faults;
  uint16_t rainEvents;
  AquaAlarm alarm;          // edge: set for one step when a new alarm condition starts
};

class AquaValveController {
 public:
  void begin(const AquaValveConfig& c, uint32_t nowMs) {
    cfg_ = c; state_ = AQUA_VS_CLOSED_IDLE; reason_ = AQUA_VR_NONE; open_ = false;
    lastCloseMs_ = nowMs - c.minSwitchMs; lastOpenMs_ = nowMs;
    rainRawSince_ = nowMs; lastRainRaw_ = false; rainActive_ = false;
    tankBadSince_ = turbBadSince_ = 0; tankBad_ = turbBad_ = false;
    tankFaultOn_ = turbFaultOn_ = false; tankGoodSince_ = turbGoodSince_ = nowMs;
    full_ = false; clear_ = false; clearSince_ = 0; unsafeSince_ = 0; flushDone_ = false;
    events_ = 0; flushStart_ = 0;
  }

  AquaValveOutput step(uint32_t now, const AquaValveInputs& in) {
    AquaAlarm alarm = AQUA_ALARM_NONE;

    // -- rain debounce -------------------------------------------------------
    if (in.rainRaw != lastRainRaw_) { lastRainRaw_ = in.rainRaw; rainRawSince_ = now; }
    uint32_t held = now - rainRawSince_;
    if (in.rainRaw && !rainActive_ && held >= cfg_.rainOnDebounceMs) { rainActive_ = true; }
    if (!in.rainRaw && rainActive_ && held >= cfg_.rainOffDebounceMs) { rainActive_ = false; }

    // -- sensor validity with timeout / recovery ------------------------------
    trackValidity(now, in.tankValid, tankBad_, tankBadSince_, tankGoodSince_, tankFaultOn_);
    trackValidity(now, in.turbValid, turbBad_, turbBadSince_, turbGoodSince_, turbFaultOn_);
    bool fault = tankFaultOn_ || turbFaultOn_;

    // -- tank full (hysteresis) ------------------------------------------------
    if (in.tankValid) {
      if (in.levelPct >= cfg_.tankFullOnPct) full_ = true;
      else if (in.levelPct <= cfg_.tankFullOffPct) full_ = false;
    }
    // -- water clarity (hysteresis) ---------------------------------------------
    if (in.turbValid) {
      if (in.turbAdc >= cfg_.clearOnAdc) { if (!clear_) clearSince_ = now; clear_ = true; unsafeSince_ = 0; }
      else if (in.turbAdc <= cfg_.unsafeBelowAdc) { if (clear_ || unsafeSince_ == 0) unsafeSince_ = now; clear_ = false; clearSince_ = 0; }
    } else { clear_ = false; clearSince_ = 0; }
    bool clearStable = clear_ && (now - clearSince_) >= cfg_.clearStableMs;
    bool unsafeConfirmed = in.turbValid && !clear_ && unsafeSince_ != 0 && (now - unsafeSince_) >= cfg_.unsafeConfirmMs;

    AquaValveState prev = state_;
    bool canOpenNow = rainActive_ && in.tankValid && in.turbValid && !full_ && !fault && clearStable &&
                      (flushDone_ || (flushStart_ && (now - flushStart_) >= cfg_.firstFlushMs)) &&
                      (now - lastCloseMs_) >= cfg_.minSwitchMs;

    // -- fault handling is global ------------------------------------------------
    if (fault && state_ != AQUA_VS_FAULT_SAFE) { setState(AQUA_VS_FAULT_SAFE, AQUA_VR_SENSOR_FAULT, now); alarm = AQUA_ALARM_SENSOR_FAULT; }

    switch (state_) {
      case AQUA_VS_CLOSED_IDLE:
        reason_ = AQUA_VR_NONE;
        if (in.rainRaw) setState(AQUA_VS_RAIN_DEBOUNCE, AQUA_VR_RAIN_NOT_CONFIRMED, now);
        break;
      case AQUA_VS_RAIN_DEBOUNCE:
        if (!in.rainRaw && !rainActive_) setState(AQUA_VS_CLOSED_IDLE, AQUA_VR_NONE, now);
        else if (rainActive_) { events_++; flushStart_ = now; flushDone_ = false; setState(AQUA_VS_FIRST_FLUSH, AQUA_VR_FIRST_FLUSH, now); }
        break;
      case AQUA_VS_FIRST_FLUSH:
        if (!rainActive_) { setState(AQUA_VS_RAIN_ENDING, AQUA_VR_RAIN_ENDED, now); break; }
        if (full_) { setState(AQUA_VS_CLOSED_TANK_FULL, AQUA_VR_TANK_FULL, now); alarm = AQUA_ALARM_TANK_FULL; break; }
        if (!flushDone_ && (now - flushStart_) >= cfg_.firstFlushMs) flushDone_ = true;
        if (canOpenNow) setState(AQUA_VS_OPEN_COLLECTING, AQUA_VR_COLLECTING, now);
        else reason_ = !flushDone_ ? AQUA_VR_FIRST_FLUSH : (!in.tankValid ? AQUA_VR_TANK_INVALID
                       : ((now - lastCloseMs_) < cfg_.minSwitchMs ? AQUA_VR_MIN_INTERVAL : AQUA_VR_WAIT_CLEAR));
        break;
      case AQUA_VS_OPEN_COLLECTING:
        reason_ = AQUA_VR_COLLECTING;
        if (full_) { setState(AQUA_VS_CLOSED_TANK_FULL, AQUA_VR_TANK_FULL, now); alarm = AQUA_ALARM_TANK_FULL; }
        else if (unsafeConfirmed) { setState(AQUA_VS_CLOSED_UNSAFE, AQUA_VR_UNSAFE_WATER, now); alarm = AQUA_ALARM_UNSAFE_WATER; }
        else if (!rainActive_ && (now - lastOpenMs_) >= cfg_.minSwitchMs) setState(AQUA_VS_RAIN_ENDING, AQUA_VR_RAIN_ENDED, now);
        break;
      case AQUA_VS_CLOSED_TANK_FULL:
        if (!rainActive_) setState(AQUA_VS_RAIN_ENDING, AQUA_VR_RAIN_ENDED, now);
        else if (canOpenNow) setState(AQUA_VS_OPEN_COLLECTING, AQUA_VR_COLLECTING, now);
        break;
      case AQUA_VS_CLOSED_UNSAFE:
        if (!rainActive_) setState(AQUA_VS_RAIN_ENDING, AQUA_VR_RAIN_ENDED, now);
        else if (canOpenNow) setState(AQUA_VS_OPEN_COLLECTING, AQUA_VR_COLLECTING, now);
        break;
      case AQUA_VS_RAIN_ENDING:
        setState(AQUA_VS_CLOSED_IDLE, AQUA_VR_NONE, now);
        break;
      case AQUA_VS_FAULT_SAFE:
        reason_ = AQUA_VR_SENSOR_FAULT;
        if (!fault) setState(AQUA_VS_CLOSED_IDLE, AQUA_VR_NONE, now);
        break;
    }
    (void)prev;

    // commanded output
    bool wantOpen = (state_ == AQUA_VS_OPEN_COLLECTING) || (state_ == AQUA_VS_FAULT_SAFE && cfg_.failSafeOpen);
    if (wantOpen && !open_) lastOpenMs_ = now;
    if (!wantOpen && open_) lastCloseMs_ = now;
    open_ = wantOpen;

    AquaValveOutput o;
    o.state = state_; o.reason = reason_; o.valveOpen = open_; o.rainActive = rainActive_;
    o.firstFlush = (state_ == AQUA_VS_FIRST_FLUSH && !flushDone_);
    o.tankFull = full_; o.waterClear = clear_;
    o.faults = (tankFaultOn_ ? AQUA_RFAULT_ULTRASONIC : 0) | (turbFaultOn_ ? AQUA_RFAULT_TURBIDITY : 0) |
               (fault ? AQUA_RFAULT_SENSOR_TIMEOUT : 0);
    o.rainEvents = events_; o.alarm = alarm;
    return o;
  }

  AquaValveState state() const { return state_; }

 private:
  void setState(AquaValveState s, AquaValveReason r, uint32_t now) {
    state_ = s; reason_ = r; (void)now;
    if (s == AQUA_VS_CLOSED_IDLE) { flushDone_ = false; flushStart_ = 0; }
  }
  void trackValidity(uint32_t now, bool valid, bool& bad, uint32_t& badSince, uint32_t& goodSince, bool& faultOn) {
    if (valid) {
      if (bad) { bad = false; goodSince = now; }
      if (faultOn && (now - goodSince) >= cfg_.faultRecoverMs) faultOn = false;
    } else {
      if (!bad) { bad = true; badSince = now; }
      if (!faultOn && (now - badSince) >= cfg_.sensorTimeoutMs) faultOn = true;
    }
  }

  AquaValveConfig cfg_;
  AquaValveState state_ = AQUA_VS_CLOSED_IDLE;
  AquaValveReason reason_ = AQUA_VR_NONE;
  bool open_ = false, lastRainRaw_ = false, rainActive_ = false;
  bool tankBad_ = false, turbBad_ = false, tankFaultOn_ = false, turbFaultOn_ = false;
  bool full_ = false, clear_ = false, flushDone_ = false;
  uint32_t lastCloseMs_ = 0, lastOpenMs_ = 0, rainRawSince_ = 0, tankBadSince_ = 0, turbBadSince_ = 0;
  uint32_t tankGoodSince_ = 0, turbGoodSince_ = 0, clearSince_ = 0, unsafeSince_ = 0, flushStart_ = 0;
  uint16_t events_ = 0;
};

/*
 * Non-blocking buzzer: one alarm pattern at a time, per-alarm cool-down so the
 * buzzer never sounds continuously.  Call alarm() on an edge, tick() each loop.
 */
class AquaBuzzer {
 public:
  void begin(uint32_t cooldownMs) { cool_ = cooldownMs; memset(last_, 0, sizeof(last_)); on_ = false; }
  void alarm(AquaAlarm a, uint32_t now) {
    if (a == AQUA_ALARM_NONE) return;
    if (last_[a] != 0 && (now - last_[a]) < cool_) return;
    if (step_ < count_) return;                       // pattern already running
    last_[a] = now ? now : 1;
    switch (a) {                                      // durations: on, off, on, off ... (ms)
      case AQUA_ALARM_TANK_FULL:     load(3, 150, 150); break;     // 3 short
      case AQUA_ALARM_UNSAFE_WATER:  load(2, 400, 200); break;     // 2 medium
      case AQUA_ALARM_SENSOR_FAULT:  load(1, 1200, 0); break;      // 1 long
      case AQUA_ALARM_LINK_LOST:     load(1, 150, 0); break;       // 1 short
      default: break;
    }
    stepStart_ = now; on_ = count_ > 0;
  }
  // returns desired buzzer level
  bool tick(uint32_t now) {
    if (step_ >= count_) { on_ = false; return false; }
    uint32_t dur = (step_ % 2 == 0) ? onMs_ : offMs_;
    if ((now - stepStart_) >= dur) { step_++; stepStart_ = now; }
    on_ = (step_ < count_) && (step_ % 2 == 0);
    return on_;
  }
  bool active() const { return step_ < count_; }
 private:
  void load(uint8_t pulses, uint16_t on, uint16_t off) { onMs_ = on; offMs_ = off; count_ = pulses * 2 - (off ? 0 : 1); step_ = 0; }
  uint32_t cool_ = 600000, last_[8], stepStart_ = 0;
  uint16_t onMs_ = 0, offMs_ = 0; uint8_t count_ = 0, step_ = 0; bool on_ = false;
};
