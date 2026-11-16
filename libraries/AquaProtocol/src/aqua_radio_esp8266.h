/*
 * ESP8266 (ESP-01S) ESP-NOW radio HAL for AquaNodeLink.
 * Interrupt/callback context only copies frames into a small ring; all protocol
 * work happens in loop() via pump().  Nothing here blocks.
 */
#pragma once
#if !defined(ESP8266)
#error "aqua_radio_esp8266.h is for ESP8266 targets only"
#endif
#include <Arduino.h>
#include <ESP8266WiFi.h>
extern "C" {
#include <espnow.h>
#include <user_interface.h>
}
#include "aqua_protocol.h"
#include "aqua_secrets.h"

class AquaRadio8266 {
 public:
  static const uint8_t RXQ = 4, RXMAX = 64;

  bool begin() {
    self_ = this;
    WiFi.persistent(false);
    WiFi.mode(WIFI_STA);
    WiFi.disconnect();
    delay(50);                                   // one-time, before pulse counting matters
    if (esp_now_init() != 0) return false;
    esp_now_set_self_role(ESP_NOW_ROLE_COMBO);   // COMBO: must both send and receive ACKs
#if AQUA_ESPNOW_ENCRYPT
    static uint8_t kok[16] = AQUA_PMK;
    esp_now_set_kok(kok, 16);
#endif
    esp_now_register_send_cb(&AquaRadio8266::onSent);
    esp_now_register_recv_cb(&AquaRadio8266::onRecv);
    static uint8_t bc[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
    esp_now_add_peer(bc, ESP_NOW_ROLE_COMBO, 1, nullptr, 0);
    return true;
  }

  uint32_t nowMs() { return millis(); }
  uint32_t random32() { return RANDOM_REG32 ^ (micros() * 2654435761u); }

  void setChannel(uint8_t c) {
    channel_ = c;
    wifi_set_channel(c);
    static uint8_t bc[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
    esp_now_set_peer_channel(bc, c);
    if (hasGw_) esp_now_set_peer_channel(gw_, c);
  }

  void setGateway(const uint8_t mac[6]) {
    if (hasGw_) esp_now_del_peer(gw_);
    memcpy(gw_, mac, 6);
#if AQUA_ESPNOW_ENCRYPT
    static uint8_t lmk[16] = AQUA_LMK;
    esp_now_add_peer(gw_, ESP_NOW_ROLE_COMBO, channel_, lmk, 16);
#else
    esp_now_add_peer(gw_, ESP_NOW_ROLE_COMBO, channel_, nullptr, 0);
#endif
    hasGw_ = true;
  }
  void clearGateway() { if (hasGw_) esp_now_del_peer(gw_); hasGw_ = false; }

  bool sendBroadcast(const uint8_t* d, size_t n) {
    static uint8_t bc[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
    return send(bc, d, n);
  }
  bool sendUnicast(const uint8_t* d, size_t n) { return hasGw_ && send(gw_, d, n); }

  // Deliver queued frames to the link layer (call every loop()).
  template <class LINK> void pump(LINK& link) {
    while (tail_ != head_) {
      Rx& r = q_[tail_ % RXQ];
      link.onReceive(r.mac, r.data, r.len);
      tail_++;
    }
  }

  uint32_t rxOverruns() const { return overruns_; }

 private:
  struct Rx { uint8_t mac[6]; uint8_t len; uint8_t data[RXMAX]; };

  bool send(uint8_t* mac, const uint8_t* d, size_t n) {
    if (txBusy_ && (millis() - txStart_) < 20) return false;   // previous send callback not yet seen
    txBusy_ = true; txStart_ = millis();
    return esp_now_send(mac, const_cast<uint8_t*>(d), (uint8_t)n) == 0;
  }
  static void onSent(uint8_t*, uint8_t) { if (self_) self_->txBusy_ = false; }
  static void onRecv(uint8_t* mac, uint8_t* data, uint8_t len) {
    AquaRadio8266* s = self_;
    if (!s || len > RXMAX) return;
    if ((uint8_t)(s->head_ - s->tail_) >= RXQ) { s->overruns_++; return; }
    Rx& r = s->q_[s->head_ % RXQ];
    memcpy(r.mac, mac, 6); r.len = len; memcpy(r.data, data, len);
    s->head_++;
  }

  static AquaRadio8266* self_;
  Rx q_[RXQ];
  volatile uint8_t head_ = 0, tail_ = 0;
  volatile bool txBusy_ = false;
  volatile uint32_t txStart_ = 0, overruns_ = 0;
  uint8_t gw_[6] = {0}; bool hasGw_ = false; uint8_t channel_ = 1;
};
AquaRadio8266* AquaRadio8266::self_ = nullptr;
