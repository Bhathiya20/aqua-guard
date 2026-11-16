/*
===========================================================
  AQUA GUARD - GENERIC FLOW NODE (ESP32-C3 SuperMini + YF-S201)
  ONE binary for every node: no node id, no per-node compile.

  Identity   MAC + a random 12-symbol pairing code generated at first boot (kept in NVS).
             Serial prints MAC, code, the QR payload and an ASCII QR at every boot.
  Pairing    the owner adds the node in the gateway portal / web app (scan the QR or type MAC + code);
             the node then joins automatically (authenticated handshake, see aqua_node_link.h).
  Counting   the pulse ISR never resets the counter, so nothing is lost while the radio scans or retries.

  Pins (breadboard, confirmed by the project owner)
    GPIO4  YF-S201 pulse, yellow wire directly to GPIO4 + 1k pull-up to 3.3 V, FALLING edge, 1000 us glitch filter
    GPIO3  battery sense (ADC1_CH3): R_TOP/R_BOT divider + 100 nF (now 220k/100k)  ->  Vbat = mV * (R_TOP+R_BOT)/R_BOT
    GPIO8  onboard LED        GPIO9  BOOT button (strapping pin: only used as an input AFTER boot)

  Build: board "ESP32C3 Dev Module", USB CDC On Boot = Enabled, Arduino-ESP32 core 3.3.x.
         Libraries: AquaProtocol (this repo); the ASCII QR uses the encoder built into the ESP32 core.  Run  python tools/generate_secrets.py  first.

  LED     UNPAIRED  fast blink          PAIRED  solid for 10 s after joining, then off (short flash per data frame)
          LINK LOST slow blink
  BOOT    hold  5 s -> forget "paired" and restart (re-enter pairing)      hold 15 s -> factory reset (clears pairing + calibration; the
          pairing CODE is kept because it is on the sticker).  New code only via serial:  regen-code  then  regen-code confirm
===========================================================
*/
#include <WiFi.h>
#include <esp_wifi.h>
#include <qrcode.h>                       // esp_qrcode (part of the ESP32 core): ASCII QR on serial; before aqua_identity_esp32.h
#include <aqua_secrets.h>
#if defined(AQUA_SECRETS_ARE_EXAMPLE)
#error "aqua_secrets.h is the example template: run  python tools/generate_secrets.py"
#endif
#include <AquaProtocol.h>
#include <aqua_radio_esp32.h>
#include <aqua_identity_esp32.h>

// ---- hardware ---------------------------------------------------------------------------------
#define FLOW_PIN          4
#define VBAT_PIN          3
#define LED_PIN           8
#define BTN_PIN           9
// Verified on the owner's board: the onboard LED is active-HIGH.
#define LED_ACTIVE_LOW    0
// Battery divider: AS BUILT 220k / 100k.  When the board is rebuilt with 330k / 100k change VBAT_DIV_R_TOP only.
#ifndef VBAT_DIV_R_TOP
#define VBAT_DIV_R_TOP    220000UL        // ohm
#endif
#ifndef VBAT_DIV_R_BOT
#define VBAT_DIV_R_BOT    100000UL        // ohm
#endif
#define FLOW_GLITCH_US    1000UL          // ignore edges closer than this (max real rate ~140 Hz = 7 ms period)
#define VBAT_SAMPLES      12

// ---- behaviour (build-time configuration) -------------------------------------------------------
#ifndef AQUA_SAMPLE_MS
#define AQUA_SAMPLE_MS    2000UL          // measurement interval; change here (kept off the radio path)
#endif
#define DEFAULT_PPL_X10   4500            // 450.0 pulses/L (YF-S201 nominal, +-10 %); the gateway can override it per node
#define JOINED_LED_MS     10000UL
#define HOLD_REPAIR_MS    5000UL
#define HOLD_RESET_MS     15000UL

static const uint8_t PSK[16] = AQUA_PSK;

static portMUX_TYPE g_mux = portMUX_INITIALIZER_UNLOCKED;
static volatile uint32_t g_pulses = 0;    // free-running, never reset
static volatile uint32_t g_lastEdgeUs = 0;
void IRAM_ATTR flowPulse() {
  uint32_t t = (uint32_t)micros();
  if ((uint32_t)(t - g_lastEdgeUs) < FLOW_GLITCH_US) return;               // glitch / bounce filter
  g_lastEdgeUs = t;
  portENTER_CRITICAL_ISR(&g_mux); g_pulses++; portEXIT_CRITICAL_ISR(&g_mux);
}
static uint32_t readPulses() { portENTER_CRITICAL(&g_mux); uint32_t c = g_pulses; portEXIT_CRITICAL(&g_mux); return c; }

AquaIdentity identity;
AquaRadio32 radio;
AquaNodeLink<AquaRadio32, 24, 64> nodeLink;
AquaFlowMeter meter;
uint32_t nextSample = 0, pulseBase = 0, joinedAtMs = 0, flashUntil = 0, lastFramesSent = 0;
uint16_t pplX10 = DEFAULT_PPL_X10;

// ---- battery --------------------------------------------------------------------------------------
// Returns AQUA_BATT_NONE (reported as null, "no battery") when there is no divider on the pin: a floating pin wanders and follows the flow
// pulses, so the samples are spread over ~24 ms and checked for stability and plausibility in aqua_battery.h (host-tested).
static uint16_t readBatteryMv() {
  uint16_t s[VBAT_SAMPLES];
  for (int i = 0; i < VBAT_SAMPLES; i++) { s[i] = (uint16_t)analogReadMilliVolts(VBAT_PIN); delay(2); }
  return aquaBatteryMv(s, VBAT_SAMPLES, VBAT_DIV_R_TOP, VBAT_DIV_R_BOT);
}

// ---- LED -------------------------------------------------------------------------------------------
static void led(bool on) { digitalWrite(LED_PIN, (on != (LED_ACTIVE_LOW != 0)) ? HIGH : LOW); }
static void updateLed(uint32_t now) {
  bool on;
  bool online = nodeLink.state() == AquaNodeLink<AquaRadio32, 24, 64>::ONLINE;
  if (online) {
    on = (uint32_t)(now - joinedAtMs) < JOINED_LED_MS;                      // solid "I have joined", then dark
    if (nodeLink.stats().framesSent != lastFramesSent) { lastFramesSent = nodeLink.stats().framesSent; flashUntil = now + 40; }
    if ((int32_t)(flashUntil - now) > 0) on = true;                          // short flash per sent frame
  } else if (!identity.wasPaired()) on = (now / 100) % 2 == 0;               // UNPAIRED: fast blink
  else on = (now / 1000) % 2 == 0;                                           // LINK LOST: slow blink
  led(on);
}

// ---- BOOT button ------------------------------------------------------------------------------------
static void handleButton(uint32_t now) {
  static uint32_t downAt = 0; static bool armed = false, blinked5 = false;
  bool down = digitalRead(BTN_PIN) == LOW;
  if (!armed) { if (!down) armed = true; return; }                          // ignore a press that was already held at boot
  if (down) {
    if (!downAt) downAt = now;
    uint32_t held = now - downAt;
    if (held >= HOLD_RESET_MS) {
      Serial.println("[BTN] 15 s: FACTORY RESET - pairing state cleared, pairing CODE KEPT (sticker stays valid), restarting");
      identity.factoryReset(); delay(200); ESP.restart();
    }
    if (held >= HOLD_REPAIR_MS && !blinked5) { blinked5 = true; led(true); }
  } else {
    if (downAt && now - downAt >= HOLD_REPAIR_MS && now - downAt < HOLD_RESET_MS) {
      Serial.println("[BTN] 5 s: re-enter pairing (forgetting 'paired'), restarting");
      identity.setPaired(false); delay(200); ESP.restart();
    }
    downAt = 0; blinked5 = false;
  }
}

void setup() {
  pinMode(LED_PIN, OUTPUT); led(false);
  pinMode(BTN_PIN, INPUT_PULLUP);
  Serial.begin(115200);
  { uint32_t w = millis(); while (!Serial && millis() - w < 3000) delay(10); }   // USB-CDC: wait up to 3 s for the host so the banner is not lost
  delay(300);
  analogSetPinAttenuation(VBAT_PIN, ADC_11db);
  pinMode(FLOW_PIN, INPUT);                                                  // external 1k pull-up: no internal pull
  attachInterrupt(digitalPinToInterrupt(FLOW_PIN), flowPulse, FALLING);

  if (!identity.begin(AQUA_DEV_FLOW)) { Serial.println("NVS unusable - restarting"); delay(1000); ESP.restart(); }
  { uint8_t k[16] = AQUA_PSK; if (aquaKeyIsWeak(k)) { Serial.println("FATAL: PSK is all-zero (example template). Run tools/generate_secrets.py"); for (;;) delay(1000); } }
  identity.printBanner(Serial, "AQUA GUARD FLOW NODE (ESP32-C3)");
  pplX10 = identity.pplX10(DEFAULT_PPL_X10);

  if (!radio.begin()) { Serial.println("[ESP-NOW] init failed - restarting"); delay(1000); ESP.restart(); }
  // TX power is now a per-node level pushed by the gateway (8.5 / 11 / 13 / 15 dBm). Default 8.5: hardware-verified need on the SuperMini
  // (at full power no frame reached the gateway). An unpaired node has no stored level yet, so it joins at the default.
  WiFi.setTxPower((wifi_power_t)aquaTxQuarterDbm(identity.cfgFlags()));
  Serial.printf("[RADIO] TX power %u.%u dBm\n", aquaTxDbmX10(identity.cfgFlags()) / 10, aquaTxDbmX10(identity.cfgFlags()) % 10);
  nodeLink.begin(&radio, identity.mac(), AQUA_DEV_FLOW, identity.pairingKey(), PSK, 0);

  uint32_t now = millis();
  meter.begin(pplX10 / 10.0f, now, readPulses());
  nextSample = now + AQUA_SAMPLE_MS;
  Serial.printf("Boot session %08lX, %.1f pulses/L, %s\n", (unsigned long)nodeLink.bootId(), pplX10 / 10.0f,
                identity.wasPaired() ? "previously paired" : "UNPAIRED (add this node in the gateway portal)");
}

void loop() {
  radio.pump(nodeLink);
  uint32_t now = millis();

  if ((int32_t)(now - nextSample) >= 0) {
    portENTER_CRITICAL(&g_mux); uint32_t cnt = g_pulses; uint32_t t = millis(); portEXIT_CRITICAL(&g_mux);
    if (nodeLink.consumeJoinEvent()) {                                        // first join of this boot: the volume record starts here
      pulseBase = cnt; meter.begin(pplX10 / 10.0f, t, 0);
      joinedAtMs = t; identity.setPaired(true);
      Serial.printf("[PAIR] joined the gateway (internal slot %u) - counting from here\n", nodeLink.slot());
    }
    AquaFlowPayload p;
    meter.compute(t, cnt - pulseBase, digitalRead(FLOW_PIN) == LOW, t / 1000, &p);
    p.batteryMv = readBatteryMv();
    nodeLink.setStatus(p.status);
    uint32_t seq = nodeLink.queueData(&p, sizeof(p));                         // returns 0 and queues nothing until joined
    nextSample += AQUA_SAMPLE_MS;
    if ((int32_t)(now - nextSample) > (int32_t)AQUA_SAMPLE_MS) nextSample = now + AQUA_SAMPLE_MS;

    const char* st = nodeLink.state() == 1 ? "PAIRED" : nodeLink.state() == 2 ? "JOINING" : "SCAN";
    char battTxt[12];
    if (p.batteryMv == AQUA_BATT_NONE) strcpy(battTxt, "none"); else snprintf(battTxt, sizeof(battTxt), "%umV", p.batteryMv);   // no divider: "none", sent as null
    Serial.printf("up=%lus %s ch=%u seq=%lu pend=%u | %.3f L/min cum=%lu batt=%s\n", (unsigned long)(millis() / 1000), st, nodeLink.channel(), (unsigned long)seq,
                  nodeLink.pending(), p.flowMlPerMin / 1000.0f, (unsigned long)p.cumPulses, battTxt);
    if (nodeLink.state() == 0) { const auto& s = nodeLink.stats(); Serial.printf("[LINK] scanRounds=%lu sendFail=%lu rejectedRx=%lu announces=%lu\n", (unsigned long)s.scanRounds, (unsigned long)s.sendFailures, (unsigned long)s.rejectedFrames, (unsigned long)s.announces); }
  }

  uint16_t cfgPpl; uint8_t cfgFlags;                                          // configuration pushed by the gateway registry
  if (nodeLink.takeConfig(&cfgPpl, &cfgFlags)) {
    if (cfgPpl >= 1000 && cfgPpl <= 60000 && cfgPpl != pplX10) {
      pplX10 = cfgPpl; identity.setPplX10(cfgPpl); meter.begin(pplX10 / 10.0f, millis(), readPulses() - pulseBase);
      Serial.printf("[CFG] calibration now %.1f pulses/L\n", pplX10 / 10.0f);
    }
    if (cfgFlags != identity.cfgFlags()) {
      identity.setCfgFlags(cfgFlags);
      WiFi.setTxPower((wifi_power_t)aquaTxQuarterDbm(cfgFlags));
      Serial.printf("[CFG] TX power now %u.%u dBm\n", aquaTxDbmX10(cfgFlags) / 10, aquaTxDbmX10(cfgFlags) % 10);
    }
  }

  nodeLink.tick();
  if (nodeLink.unpairedByGateway()) { identity.setPaired(false); Serial.println("[PAIR] removed by the gateway - restarting as UNPAIRED"); }
  if (nodeLink.rebootRequested()) { delay(100); ESP.restart(); }
  handleButton(now);
  if (identity.pollSerial(Serial, Serial, "AQUA GUARD FLOW NODE (ESP32-C3)")) { delay(200); ESP.restart(); }   // regen-code
  updateLed(now);
  yield();
}
