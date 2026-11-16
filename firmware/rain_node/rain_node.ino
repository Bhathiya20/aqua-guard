/*
===========================================================
  AQUA GUARD - RAINWATER MONITORING NODE (ESP32)
  Target: Arduino-ESP32 core 3.3.x

  Sensors : rain (digital), HC-SR04 ultrasonic, analog turbidity
  Outputs : relay (valve), buzzer, 3 LEDs
  Link    : auto channel discovery + reliable ESP-NOW (AquaProtocol)
  Pairing : same registry pairing as the flow nodes (type=rain, shown as "Rain N"): MAC + random pairing code
            printed on serial with a QR payload at every boot; add it in the gateway portal / web app.
            Telemetry keeps its existing 'rainwater' MQTT topic.
            DevKit BOOT button (GPIO0) + onboard LED (GPIO2), neither used by anything else in this sketch:
              LED   UNPAIRED fast blink | PAIRED solid 10 s after joining, then off with a short flash per data frame | LINK LOST slow blink
              BOOT  hold 5 s (after boot) -> forget "paired", restart (re-pair)
                    hold 15 s -> factory reset: clears pairing state, the pairing CODE IS KEPT (it is on the sticker)
              Serial  "regen-code" then "regen-code confirm" -> new code (prints it + QR; a new sticker is needed)
            GPIO0 / GPIO2 are strapping pins: they are only used as input / output after boot.
            The onboard LED polarity (active HIGH on DevKit V1) is assumed, not verified on this board.

  SAFETY: the valve is NEVER opened just because rain is detected.  See
  aqua_valve.h for the interlock state machine.  The relay output is DISABLED
  by default (AQUA_VALVE_OUTPUT_ENABLE = 0, "dry run"): the logic runs and is
  reported on the dashboard, but the relay pin is held de-energised.  Enable
  the output only after the bench checklist in docs/WIRING_AND_SAFETY.md.
===========================================================
*/
#include <WiFi.h>
#include <qrcode.h>                       // esp_qrcode (ESP32 core): ASCII QR on serial
#include <aqua_secrets.h>
#if defined(AQUA_SECRETS_ARE_EXAMPLE)
#error "aqua_secrets.h is the example template: run  python tools/generate_secrets.py"
#endif
#include <aqua_protocol.h>
#include <aqua_tx_window.h>
#include <aqua_node_link.h>
#include <aqua_valve.h>
#include <aqua_radio_esp32.h>
#include <aqua_identity_esp32.h>

// ---- pins (unchanged from the original project) ------------------------------
#define RAIN_SENSOR_PIN 27     // digital rain module, LOW = wet
#define TRIG_PIN        5
#define ECHO_PIN        18     // HC-SR04 ECHO is 5 V: use a divider / level shifter to 3.3 V
#define TURBIDITY_PIN   35     // input-only ADC1 pin
#define RELAY_PIN       26
#define GREEN_LED       14
#define YELLOW_LED      12
#define RED_LED         13
#define BUZZER_PIN      15     // assumed ACTIVE buzzer (HIGH = sound)
#define BTN_PIN         0      // DevKit BOOT button (strapping pin: input only after boot)
#define STATUS_LED_PIN  2      // DevKit onboard LED (strapping pin)
#define STATUS_LED_ACTIVE_LOW 0
#define JOINED_LED_MS   10000UL
#define HOLD_REPAIR_MS  5000UL
#define HOLD_RESET_MS   15000UL

// ---- relay / valve plumbing configuration --------------------------------------
#define RELAY_ACTIVE_LOW        1   // 1: module energises when pin is LOW
#define VALVE_ENERGIZE_TO_OPEN  1   // 1: normally-closed valve (power to open). 0: power to close.
#define VALVE_FAILSAFE_OPEN     0   // 0: command CLOSED on sensor fault (recommended). 1 only if your plumbing has an overflow
#ifndef AQUA_VALVE_OUTPUT_ENABLE
#define AQUA_VALVE_OUTPUT_ENABLE 0  // 0 = DRY RUN: relay never energised.  Set 1 only after bench validation.
#endif
#define BUZZER_ENABLE           1
#define BUZZER_COOLDOWN_MS      600000UL   // same alarm repeats at most every 10 min

// ---- tank geometry / thresholds ----------------------------------------------
static const float TANK_HEIGHT_CM      = 30.0f;  // sensor face to tank bottom (existing value; measure yours)
static const float US_MIN_VALID_CM     = 2.0f;   // HC-SR04 blind zone
static const float US_MAX_VALID_CM     = TANK_HEIGHT_CM + 10.0f;
static const uint32_t SEND_INTERVAL_MS = 2000;

#if RELAY_ACTIVE_LOW
#define RELAY_ON  LOW
#define RELAY_OFF HIGH
#else
#define RELAY_ON  HIGH
#define RELAY_OFF LOW
#endif

static const uint8_t PSK[16] = AQUA_PSK;

AquaIdentity identity;
AquaRadio32 radio;
AquaNodeLink<AquaRadio32, 24, 64> nodeLink;
AquaValveController valve;
AquaBuzzer buzzer;

// ---- ultrasonic median filter (5 samples, 100 ms apart, never blocks > 25 ms) ---
static float usSamples[5];
static bool  usValid[5];
static uint8_t usKind[5];        // 0 = valid, 1 = far / no echo (nothing in front), 2 = bad (too close, echo stuck)
static bool g_outOfRange = false;
static uint8_t usIdx = 0;
static uint32_t lastUs = 0, lastLogic = 0, lastSend = 0, linkDownSince = 0;
static bool lastLinkUp = false;
static float g_distanceCm = 0; static bool g_distValid = false;
static float g_levelPct = 0;

static float readDistanceOnce(bool* ok, uint8_t* kind) {
  digitalWrite(TRIG_PIN, LOW); delayMicroseconds(3);
  digitalWrite(TRIG_PIN, HIGH); delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  unsigned long d = pulseIn(ECHO_PIN, HIGH, 25000UL);          // 25 ms max
  if (d == 0) { *ok = false; *kind = digitalRead(ECHO_PIN) ? 2 : 1; return 0; }   // no echo within 25 ms: open space in front (echo line idle low), unless the line is stuck high
  float cm = d * 0.0343f / 2.0f;
  *ok = (cm >= US_MIN_VALID_CM && cm <= US_MAX_VALID_CM);
  *kind = *ok ? 0 : (cm > US_MAX_VALID_CM ? 1 : 2);
  return cm;
}

static void sampleUltrasonic() {
  bool ok; uint8_t kind = 0; float cm = readDistanceOnce(&ok, &kind);
  usSamples[usIdx] = cm; usValid[usIdx] = ok; usKind[usIdx] = kind; usIdx = (usIdx + 1) % 5;
  int n = 0; float v[5];
  for (int i = 0; i < 5; i++) if (usValid[i]) v[n++] = usSamples[i];
  if (n < 3) {
    g_distValid = false;
    int far = 0, bad = 0; for (int i = 0; i < 5; i++) { if (usKind[i] == 1) far++; else if (usKind[i] == 2) bad++; }
    g_outOfRange = (far >= 3 && bad == 0);                      // nothing in range but the sensor itself answers sanely
    return;
  }
  g_outOfRange = false;                   // majority invalid => INVALID, never "0 %"
  for (int i = 1; i < n; i++) { float k = v[i]; int j = i - 1; while (j >= 0 && v[j] > k) { v[j + 1] = v[j]; j--; } v[j + 1] = k; }
  g_distanceCm = v[n / 2]; g_distValid = true;
  float lvl = (TANK_HEIGHT_CM - g_distanceCm) / TANK_HEIGHT_CM * 100.0f;
  g_levelPct = constrain(lvl, 0.0f, 100.0f);
}

static uint16_t readTurbidityAdc(bool* ok) {
  uint32_t sum = 0; for (int i = 0; i < 8; i++) sum += analogRead(TURBIDITY_PIN);
  uint16_t adc = (uint16_t)(sum / 8);
  *ok = (adc > 20 && adc < 4090);                               // 0 / full-scale => disconnected or shorted
  return adc;
}

static void driveRelay(bool open) {
#if AQUA_VALVE_OUTPUT_ENABLE
  bool energise = VALVE_ENERGIZE_TO_OPEN ? open : !open;
  digitalWrite(RELAY_PIN, energise ? RELAY_ON : RELAY_OFF);
#else
  (void)open;
  digitalWrite(RELAY_PIN, RELAY_OFF);                            // DRY RUN: relay stays de-energised
#endif
}

static uint32_t joinedAtMs = 0, flashUntil = 0, lastFramesSent = 0;

static void statusLed(bool on) { digitalWrite(STATUS_LED_PIN, (on != (STATUS_LED_ACTIVE_LOW != 0)) ? HIGH : LOW); }
static void updateStatusLed(uint32_t now) {
  bool on;
  if (nodeLink.state() == 1) {                                                 // ONLINE
    on = (uint32_t)(now - joinedAtMs) < JOINED_LED_MS;
    if (nodeLink.stats().framesSent != lastFramesSent) { lastFramesSent = nodeLink.stats().framesSent; flashUntil = now + 40; }
    if ((int32_t)(flashUntil - now) > 0) on = true;
  } else if (!identity.wasPaired()) on = (now / 100) % 2 == 0;                 // UNPAIRED: fast blink
  else on = (now / 1000) % 2 == 0;                                             // LINK LOST: slow blink
  statusLed(on);
}

static void handleButton(uint32_t now) {
  static uint32_t downAt = 0; static bool armed = false, blinked5 = false;
  bool down = digitalRead(BTN_PIN) == LOW;
  if (!armed) { if (!down) armed = true; return; }                             // ignore a press already held at boot
  if (down) {
    if (!downAt) downAt = now;
    uint32_t held = now - downAt;
    if (held >= HOLD_RESET_MS) {
      Serial.println("[BTN] 15 s: FACTORY RESET - pairing state cleared, pairing CODE KEPT (sticker stays valid), restarting");
      identity.factoryReset(); delay(200); ESP.restart();
    }
    if (held >= HOLD_REPAIR_MS && !blinked5) { blinked5 = true; statusLed(true); }
  } else {
    if (downAt && now - downAt >= HOLD_REPAIR_MS && now - downAt < HOLD_RESET_MS) {
      Serial.println("[BTN] 5 s: re-enter pairing (forgetting 'paired'), restarting");
      identity.setPaired(false); delay(200); ESP.restart();
    }
    downAt = 0; blinked5 = false;
  }
}

static AquaValveOutput g_out;
static uint16_t g_turbAdc = 0; static bool g_turbValid = false; static bool g_rainRaw = false;

void setup() {
  digitalWrite(RELAY_PIN, RELAY_OFF);                            // de-energised BEFORE the pin becomes an output
  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, RELAY_OFF);
  Serial.begin(115200);
  pinMode(RAIN_SENSOR_PIN, INPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(TURBIDITY_PIN, INPUT);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(GREEN_LED, OUTPUT); pinMode(YELLOW_LED, OUTPUT); pinMode(RED_LED, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT); digitalWrite(BUZZER_PIN, LOW);
  pinMode(STATUS_LED_PIN, OUTPUT); statusLed(false);
  pinMode(BTN_PIN, INPUT_PULLUP);
  analogReadResolution(12);

  Serial.printf("\n=== AQUA GUARD RAINWATER NODE (proto v%d)  valve output: %s ===\n",
                AQUA_PROTO_VERSION, AQUA_VALVE_OUTPUT_ENABLE ? "ENABLED" : "DRY-RUN (relay disabled)");
  if (!identity.begin(AQUA_DEV_RAIN)) { Serial.println("NVS unusable - restarting"); delay(1000); ESP.restart(); }
  { uint8_t k[16] = AQUA_PSK; if (aquaKeyIsWeak(k)) { Serial.println("FATAL: PSK is all-zero (example template). Run tools/generate_secrets.py"); for (;;) delay(1000); } }
  identity.printBanner(Serial, "AQUA GUARD RAIN NODE");
  if (!radio.begin()) { Serial.println("ESP-NOW init failed - restarting"); delay(1000); ESP.restart(); }
  nodeLink.begin(&radio, identity.mac(), AQUA_DEV_RAIN, identity.pairingKey(), PSK, 0);

  AquaValveConfig cfg; cfg.failSafeOpen = VALVE_FAILSAFE_OPEN;
  valve.begin(cfg, millis());
  buzzer.begin(BUZZER_COOLDOWN_MS);
}

static void runLogic(uint32_t now) {
  g_rainRaw = (digitalRead(RAIN_SENSOR_PIN) == LOW);
  g_turbAdc = readTurbidityAdc(&g_turbValid);
  AquaValveInputs in;
  in.rainRaw = g_rainRaw; in.tankValid = g_distValid; in.levelPct = g_levelPct;
  in.turbValid = g_turbValid; in.turbAdc = g_turbAdc;
  g_out = valve.step(now, in);
  driveRelay(g_out.valveOpen);

  // alarms
#if BUZZER_ENABLE
  buzzer.alarm(g_out.alarm, now);
  bool linkUp = nodeLink.state() == 1;
  if (!linkUp) { if (lastLinkUp || linkDownSince == 0) linkDownSince = now; if ((now - linkDownSince) > 60000UL) buzzer.alarm(AQUA_ALARM_LINK_LOST, now); }
  else linkDownSince = 0;
  lastLinkUp = linkUp;
  digitalWrite(BUZZER_PIN, buzzer.tick(now) ? HIGH : LOW);
#endif

  // LEDs: green = collecting, yellow = rain / first-flush, red = unsafe / fault / tank full, red blink = link down
  bool linkUp2 = nodeLink.state() == 1;
  bool redSolid = (g_out.state == AQUA_VS_CLOSED_UNSAFE) || (g_out.state == AQUA_VS_FAULT_SAFE) || g_out.tankFull;
  digitalWrite(GREEN_LED, g_out.valveOpen);
  digitalWrite(YELLOW_LED, g_out.rainActive || g_rainRaw);
  digitalWrite(RED_LED, redSolid || (!linkUp2 && ((now / 500) % 2 == 0)));
}

static void sendTelemetry(uint32_t now) {
  AquaRainPayload p; memset(&p, 0, sizeof(p));
  p.uptimeS = now / 1000;
  p.distanceMm = g_distValid ? (uint16_t)(g_distanceCm * 10.0f + 0.5f) : 0xFFFF;
  p.levelX10 = g_distValid ? (uint16_t)(g_levelPct * 10.0f + 0.5f) : 0xFFFF;
  p.turbidityAdc = g_turbAdc;
  uint16_t f = 0;
  if (g_out.rainActive) f |= AQUA_RF_RAIN;
  if (g_distValid) f |= AQUA_RF_TANK_VALID;
  if (!g_distValid && g_outOfRange) f |= AQUA_RF_OUT_OF_RANGE;
  if (g_out.tankFull) f |= AQUA_RF_TANK_FULL;
  if (g_out.waterClear) f |= AQUA_RF_WATER_CLEAR;
  if (g_out.valveOpen) f |= AQUA_RF_VALVE_OPEN;
  if (digitalRead(BUZZER_PIN)) f |= AQUA_RF_BUZZER;
  if (g_out.firstFlush) f |= AQUA_RF_FIRST_FLUSH;
  if (g_turbValid) f |= AQUA_RF_TURB_VALID;
  if (!AQUA_VALVE_OUTPUT_ENABLE) f |= AQUA_RF_DRY_RUN;
  if (nodeLink.state() != 1) f |= AQUA_RF_LINK_DEGRADED;
  p.flags = f; p.faults = g_out.faults; p.rainEvents = g_out.rainEvents;
  p.valveState = g_out.state; p.valveReason = g_out.reason;
  nodeLink.setStatus(p.faults);
  uint32_t seq = nodeLink.queueData(&p, sizeof(p));
  Serial.printf("RAIN seq=%lu %s ch=%u pend=%u | rain=%d level=%s dist=%s turb=%u(%s) valve=%s state=%u reason=%u faults=0x%02X\n",
                (unsigned long)seq, nodeLink.state() == 1 ? "ONLINE" : "SCAN", nodeLink.channel(), nodeLink.pending(), g_out.rainActive,
                g_distValid ? String(g_levelPct, 1).c_str() : "INVALID", g_distValid ? String(g_distanceCm, 1).c_str() : "INVALID",
                g_turbAdc, g_turbValid ? "ok" : "BAD", g_out.valveOpen ? "OPEN" : "closed", g_out.state, g_out.reason, g_out.faults);
}

void loop() {
  radio.pump(nodeLink);
  uint32_t now = millis();
  if (now - lastUs >= 100) { lastUs = now; sampleUltrasonic(); }
  if (now - lastLogic >= 200) { lastLogic = now; runLogic(now); }
  if (now - lastSend >= SEND_INTERVAL_MS) { lastSend = now; sendTelemetry(now); }
  nodeLink.tick();
  if (nodeLink.consumeJoinEvent()) { joinedAtMs = now; identity.setPaired(true); Serial.printf("[PAIR] joined the gateway (internal slot %u)\n", nodeLink.slot()); }
  if (nodeLink.unpairedByGateway()) { identity.setPaired(false); Serial.println("[PAIR] removed by the gateway - restarting as UNPAIRED"); }
  if (nodeLink.rebootRequested()) { delay(100); ESP.restart(); }
  handleButton(now);
  if (identity.pollSerial(Serial, Serial, "AQUA GUARD RAIN NODE")) { delay(200); ESP.restart(); }   // regen-code
  updateStatusLed(now);
  delay(1);
}
