/*
  AQUA GUARD - ESP32-C3 SuperMini bench test for the flow node AS BUILT

  Circuit under test (same as Node 1):
    YF-S201 yellow -> GPIO4,  1 k pull-up GPIO4 -> 3.3 V,  common GND,  sensor on the 5 V buck
    GPIO8  onboard LED (active-HIGH on this board family)      GPIO9  BOOT button (LOW when pressed)
    GPIO3  battery sense (220 k / 100 k divider) - optional here

  What it prints (115200 baud, USB CDC):
    [BOOT]  chip, MAC, reset reason
    [LED]   the LED is driven HIGH for 1 s then LOW for 1 s, repeatedly: with an ACTIVE-HIGH LED it is lit while the log says "pin HIGH"
    [BTN]   BOOT = PRESSED / RELEASED on every change   (press it once)
    [FLOW]  once at boot: GPIO4 idle voltage (ADC, one-off - analogRead would switch the pin to analog mode, so it is never used afterwards)
          once per second: level now, pulses counted (falling edges, 1 ms glitch filter), raw edges, whether the pin swung HIGH AND LOW in that
            second, Hz, L/min at 450 pulses/L, and the battery pin
  This is a test: it never touches radio, NVS or the pairing code.   Type  z + Enter  to zero the count.
*/
#include <WiFi.h>
#include <esp_system.h>
#include <esp_mac.h>

#define FLOW_PIN 4
#define VBAT_PIN 3
#define LED_PIN  8
#define BTN_PIN  9
#define GLITCH_US 1000UL
static const float PPL = 450.0f;

static volatile uint32_t g_pulses = 0, g_edges = 0, g_lastUs = 0;

void IRAM_ATTR onFall() {
  g_edges++;
  uint32_t t = micros();
  if ((uint32_t)(t - g_lastUs) < GLITCH_US) return;
  g_lastUs = t;
  g_pulses++;
}

static const char* resetName(esp_reset_reason_t r) {
  switch (r) {
    case ESP_RST_POWERON: return "power-on"; case ESP_RST_SW: return "software"; case ESP_RST_PANIC: return "PANIC";
    case ESP_RST_INT_WDT: case ESP_RST_TASK_WDT: case ESP_RST_WDT: return "WATCHDOG"; case ESP_RST_BROWNOUT: return "BROWNOUT";
    case ESP_RST_USB: return "usb"; case ESP_RST_DEEPSLEEP: return "deep-sleep"; default: return "other"; }
}

static int g_idleMv = 0;
static void printBanner() {
  uint8_t m[6]; esp_read_mac(m, ESP_MAC_WIFI_STA);
  Serial.println("\n===== AQUA GUARD C3 BENCH TEST (circuit as built) =====");
  Serial.printf("[BOOT] chip=%s rev=%d  MAC=%02X:%02X:%02X:%02X:%02X:%02X  reset=%s\n", ESP.getChipModel(), (int)ESP.getChipRevision(),
                m[0], m[1], m[2], m[3], m[4], m[5], resetName(esp_reset_reason()));
  Serial.printf("[BTN] BOOT (GPIO9) reads %s now\n", digitalRead(BTN_PIN) == LOW ? "PRESSED" : "RELEASED");
  Serial.printf("[FLOW] GPIO4 idle at boot: %d mV (expected about 3.0-3.3 V with the 1 k pull-up), now %s\n", g_idleMv, digitalRead(FLOW_PIN) ? "HIGH" : "LOW");
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  pinMode(LED_PIN, OUTPUT); digitalWrite(LED_PIN, LOW);
  pinMode(BTN_PIN, INPUT_PULLUP);
  pinMode(FLOW_PIN, INPUT);                                   // external 1 k pull-up: no internal pull
  analogReadResolution(12);
  analogSetPinAttenuation(FLOW_PIN, ADC_11db);
  int idleMv = 0; for (int i = 0; i < 16; i++) idleMv += analogReadMilliVolts(FLOW_PIN);
  idleMv /= 16;
  pinMode(FLOW_PIN, INPUT);                                   // back to a digital input after the one-off ADC read
  analogSetPinAttenuation(VBAT_PIN, ADC_11db);
  WiFi.mode(WIFI_STA);
  g_idleMv = idleMv;
  printBanner();
  attachInterrupt(digitalPinToInterrupt(FLOW_PIN), onFall, FALLING);
}

void loop() {
  static uint32_t tBan = 0;
  if (millis() - tBan >= 10000) { tBan = millis(); if (tBan) printBanner(); }
  static uint32_t tLed = 0, tSec = 0, lastEdges = 0, lastPulses = 0;
  static bool ledOn = false, btnPrev = false, seenHi = false, seenLo = false;
  uint32_t now = millis();

  if (now - tLed >= 1000) {                                    // LED: 1 s HIGH, 1 s LOW
    tLed = now; ledOn = !ledOn; digitalWrite(LED_PIN, ledOn ? HIGH : LOW);
    Serial.printf("[LED] pin %s  (active-HIGH LED is %s)\n", ledOn ? "HIGH" : "LOW", ledOn ? "LIT" : "dark");
  }
  bool pressed = digitalRead(BTN_PIN) == LOW;
  if (pressed != btnPrev) { btnPrev = pressed; Serial.printf("[BTN] BOOT = %s\n", pressed ? "PRESSED" : "RELEASED"); }

  if (digitalRead(FLOW_PIN)) seenHi = true; else seenLo = true;

  if (now - tSec >= 1000) {
    uint32_t dt = now - tSec; tSec = now;
    noInterrupts(); uint32_t p = g_pulses, e = g_edges; interrupts();
    uint32_t dp = p - lastPulses, de = e - lastEdges; lastPulses = p; lastEdges = e;
    float hz = dp * 1000.0f / dt;
    uint32_t vb = 0; for (int i = 0; i < 8; i++) vb += analogReadMilliVolts(VBAT_PIN); vb /= 8;
    Serial.printf("[FLOW] level=%s pulses=%lu (+%lu/s, %lu raw edges) swing=%s%s  %.1f Hz  %.2f L/min   vbatPin=%lu mV (batt~%lu mV)\n",
                  digitalRead(FLOW_PIN) ? "HIGH" : "LOW", (unsigned long)p, (unsigned long)dp, (unsigned long)de, seenHi ? "H" : "-", seenLo ? "L" : "-",
                  hz, hz / PPL * 60.0f, (unsigned long)vb, (unsigned long)(vb * 320UL / 100UL));
    seenHi = seenLo = false;
  }
  while (Serial.available()) { if (Serial.read() == 'z') { noInterrupts(); g_pulses = g_edges = 0; interrupts(); lastPulses = lastEdges = 0; Serial.println("[FLOW] count zeroed"); } }
}
