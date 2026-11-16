/*
 * ESP32 ESP-NOW radio HAL for AquaNodeLink (used by the rainwater node).
 * Built for Arduino-ESP32 core 3.x (IDF 5.x callback signatures).
 */
#pragma once
#if !defined(ESP32)
#error "aqua_radio_esp32.h is for ESP32 targets only"
#endif
#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <esp_random.h>
#include "aqua_protocol.h"
#include "aqua_secrets.h"
#if defined(AQUA_SECRETS_ARE_EXAMPLE)
#error "aqua_secrets.h is the example template: run  python tools/generate_secrets.py"
#endif

class AquaRadio32 {
 public:
  static const uint8_t RXQ = 8, RXMAX = 64;

  bool begin() {
    self_ = this;
    WiFi.persistent(false);
    WiFi.mode(WIFI_STA);
    WiFi.disconnect();
    delay(50);
    if (esp_now_init() != ESP_OK) return false;
#if AQUA_ESPNOW_ENCRYPT
    static uint8_t pmk[16] = AQUA_PMK;
    esp_now_set_pmk(pmk);
#endif
    esp_now_register_send_cb(&AquaRadio32::onSent);
    esp_now_register_recv_cb(&AquaRadio32::onRecv);
    esp_now_peer_info_t p = {};
    memset(p.peer_addr, 0xFF, 6); p.channel = 0; p.ifidx = WIFI_IF_STA; p.encrypt = false;
    esp_now_add_peer(&p);
    return true;
  }

  uint32_t nowMs() { return millis(); }
  uint32_t random32() { return esp_random(); }
  void setChannel(uint8_t c) { esp_wifi_set_channel(c, WIFI_SECOND_CHAN_NONE); }

  void setGateway(const uint8_t mac[6]) {
    if (hasGw_) esp_now_del_peer(gw_);
    memcpy(gw_, mac, 6);
    esp_now_peer_info_t p = {};
    memcpy(p.peer_addr, gw_, 6); p.channel = 0; p.ifidx = WIFI_IF_STA;
#if AQUA_ESPNOW_ENCRYPT
    static uint8_t lmk[16] = AQUA_LMK;
    p.encrypt = true; memcpy(p.lmk, lmk, 16);
#else
    p.encrypt = false;
#endif
    hasGw_ = esp_now_add_peer(&p) == ESP_OK;
  }
  void clearGateway() { if (hasGw_) esp_now_del_peer(gw_); hasGw_ = false; }

  bool sendBroadcast(const uint8_t* d, size_t n) {
    static const uint8_t bc[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
    return esp_now_send(bc, d, n) == ESP_OK;
  }
  bool sendUnicast(const uint8_t* d, size_t n) { return hasGw_ && esp_now_send(gw_, d, n) == ESP_OK; }

  template <class LINK> void pump(LINK& link) {
    for (;;) {
      Rx r;
      portENTER_CRITICAL(&mux_);
      bool have = tail_ != head_;
      if (have) { r = q_[tail_ % RXQ]; tail_++; }
      portEXIT_CRITICAL(&mux_);
      if (!have) break;
      link.onReceive(r.mac, r.data, r.len);
    }
  }

 private:
  struct Rx { uint8_t mac[6]; uint8_t len; uint8_t data[RXMAX]; };
  static void onSent(const wifi_tx_info_t*, esp_now_send_status_t) {}
  static void onRecv(const esp_now_recv_info_t* info, const uint8_t* data, int len) {
    AquaRadio32* s = self_;
    if (!s || len > RXMAX || len <= 0 || !info) return;
    portENTER_CRITICAL(&s->mux_);
    if ((uint8_t)(s->head_ - s->tail_) < RXQ) {
      Rx& r = s->q_[s->head_ % RXQ];
      memcpy(r.mac, info->src_addr, 6); r.len = (uint8_t)len; memcpy(r.data, data, len);
      s->head_++;
    }
    portEXIT_CRITICAL(&s->mux_);
  }

  static AquaRadio32* self_;
  Rx q_[RXQ];
  volatile uint8_t head_ = 0, tail_ = 0;
  portMUX_TYPE mux_ = portMUX_INITIALIZER_UNLOCKED;
  uint8_t gw_[6] = {0}; bool hasGw_ = false;
};
AquaRadio32* AquaRadio32::self_ = nullptr;
