/*
  Aqua Guard - RAIN NODE BENCH TEST (no radio, no secrets, no pairing).
  Flash this BEFORE firmware/rain_node to check the wiring one part at a time.
  Serial monitor 115200 baud. Type a command + Enter:
    1  rain module   (prints wet/dry; yellow LED follows wet)
    2  ultrasonic    (prints cm; hold your hand ~10 cm then ~20 cm)
    3  turbidity     (prints raw ADC 0..4095; higher = clearer; compare clear vs dirty water; threshold 2500)
    4  LEDs          (green, yellow, red one after another, 1 s each)
    5  buzzer        (one 300 ms beep)
    6  relay pin     (prints the level; the relay is NEVER energised by this sketch)
    7  BOOT button   (prints UP/DOWN for 10 s; press and release BOOT)
    8  onboard LED   (blinks GPIO2: tells you if it is active-HIGH or active-LOW on your board: it should blink, not stay lit)
    0  stop / all outputs off
  Pins are identical to firmware/rain_node.  The relay stays at its OFF level (HIGH, active-LOW module) the whole time.
*/
#include <Arduino.h>

#define RAIN_SENSOR_PIN 27
#define TRIG_PIN        5
#define ECHO_PIN        18
#define TURBIDITY_PIN   35
#define RELAY_PIN       26
#define GREEN_LED       14
#define YELLOW_LED      12
#define RED_LED         13
#define BUZZER_PIN      15
#define BTN_PIN         0
#define STATUS_LED_PIN  2
#define RELAY_OFF       HIGH     // active-LOW relay module: HIGH = de-energised

static int mode = 0;
static uint32_t t0 = 0;

static float distanceCm() {
  digitalWrite(TRIG_PIN, LOW); delayMicroseconds(3);
  digitalWrite(TRIG_PIN, HIGH); delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  unsigned long d = pulseIn(ECHO_PIN, HIGH, 25000UL);
  return d == 0 ? -1.0f : d * 0.0343f / 2.0f;
}
static void allOff() {
  digitalWrite(GREEN_LED, LOW); digitalWrite(YELLOW_LED, LOW); digitalWrite(RED_LED, LOW);
  digitalWrite(BUZZER_PIN, LOW); digitalWrite(STATUS_LED_PIN, LOW);
}

void setup() {
  digitalWrite(RELAY_PIN, RELAY_OFF);          // relay level is set before the pin becomes an output
  pinMode(RELAY_PIN, OUTPUT); digitalWrite(RELAY_PIN, RELAY_OFF);
  pinMode(RAIN_SENSOR_PIN, INPUT); pinMode(ECHO_PIN, INPUT); pinMode(TURBIDITY_PIN, INPUT);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(GREEN_LED, OUTPUT); pinMode(YELLOW_LED, OUTPUT); pinMode(RED_LED, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT); pinMode(STATUS_LED_PIN, OUTPUT); pinMode(BTN_PIN, INPUT_PULLUP);
  allOff();
  Serial.begin(115200); delay(300);
  Serial.println("\n=== RAIN NODE BENCH TEST === commands: 1 rain  2 ultrasonic  3 turbidity  4 LEDs  5 buzzer  6 relay pin  7 BOOT  8 onboard LED  0 stop");
  Serial.println("The relay is never energised by this sketch.");
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c >= '0' && c <= '8') { mode = c - '0'; t0 = millis(); allOff(); Serial.printf("-- test %d --\n", mode); }
  }
  uint32_t el = millis() - t0;
  digitalWrite(RELAY_PIN, RELAY_OFF);
  switch (mode) {
    case 1: { bool wet = digitalRead(RAIN_SENSOR_PIN) == LOW; digitalWrite(YELLOW_LED, wet); Serial.printf("rain: %s (pin=%d)\n", wet ? "WET" : "dry", digitalRead(RAIN_SENSOR_PIN)); delay(400); break; }
    case 2: { float d = distanceCm(); if (d < 0) Serial.println("ultrasonic: NO ECHO (check TRIG 5 / ECHO 18 / 5 V supply / divider)"); else Serial.printf("ultrasonic: %.1f cm\n", d); delay(300); break; }
    case 3: { uint32_t s = 0; for (int i = 0; i < 16; i++) s += analogRead(TURBIDITY_PIN); uint16_t v = s / 16; Serial.printf("turbidity ADC: %u  -> %s (threshold 2500, higher = clearer)\n", v, v > 2500 ? "clear" : "NOT clear"); delay(400); break; }
    case 4: { int st = (el / 1000) % 4; digitalWrite(GREEN_LED, st == 0); digitalWrite(YELLOW_LED, st == 1); digitalWrite(RED_LED, st == 2);
              static int last = -1; if (st != last) { last = st; Serial.println(st == 0 ? "GREEN (GPIO14)" : st == 1 ? "YELLOW (GPIO12)" : st == 2 ? "RED (GPIO13)" : "all off"); } break; }
    case 5: { digitalWrite(BUZZER_PIN, el < 300); if (el > 300 && el < 330) Serial.println("buzzer beeped once (type 5 again to repeat)"); break; }
    case 6: Serial.printf("relay pin GPIO26 level = %s  (expected HIGH = de-energised)\n", digitalRead(RELAY_PIN) ? "HIGH" : "LOW"); delay(500); break;
    case 7: if (el < 10000) { Serial.printf("BOOT: %s\n", digitalRead(BTN_PIN) == LOW ? "DOWN" : "up"); delay(250); } else mode = 0; break;
    case 8: digitalWrite(STATUS_LED_PIN, (el / 500) % 2); { static int l = -1; int n = (el / 500) % 2; if (n != l) { l = n; Serial.printf("GPIO2 = %s\n", n ? "HIGH" : "LOW"); } } break;
    default: break;
  }
}
