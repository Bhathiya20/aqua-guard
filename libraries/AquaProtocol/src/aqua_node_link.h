/*
 * Sensor-node link layer: gateway channel discovery, reliable DATA delivery.
 * Platform independent; the radio is supplied through a HAL class:
 *
 *   uint32_t nowMs();                          // monotonic ms
 *   uint32_t random32();                       // hardware RNG
 *   void     setChannel(uint8_t ch);           // retune radio (non-blocking)
 *   bool     sendBroadcast(const uint8_t*, size_t);
 *   bool     sendUnicast(const uint8_t*, size_t);   // to the registered gateway
 *   void     setGateway(const uint8_t mac[6]); // register unicast peer
 *   void     clearGateway();
 *
 * Everything is driven by tick(); nothing blocks, so interrupt-driven pulse
 * counting continues during scanning and retries.
 *
 * Discovery: the node broadcasts an authenticated DISCOVERY_REQUEST on each
 * candidate channel (last known channel first, then 1..AQUA_CH_MAX), with
 * back-off between full scans.  The gateway answers with an authenticated
 * DISCOVERY_RESPONSE that states its REAL channel and MAC.  A response is
 * accepted only if the tag verifies, the nonce matches the current scan
 * round, and the stated channel equals the channel we are tuned to.
 *
 * Identity and keys (v2): the node is identified by its radio MAC and holds a pairing key Kp derived
 * from its own pairing code (aqua_join.h).  After discovery it ANNOUNCES itself; the gateway (which learnt
 * the code from the QR / manual entry) challenges it, the node answers MAC_Kp(challenge) and the gateway
 * proves it knows Kp too (ACCEPT carries slot + salt).  From then on DATA/ACK/NACK/HEARTBEAT use the per-boot
 * key Kop = KDF(KDF(Kp,'N',salt),'K',boot): copying one node's flash exposes only that node.
 * Pulses counted before the first successful join of a boot are excluded from the volume record.
 */
#pragma once
#include <stdint.h>
#include <string.h>
#include "aqua_protocol.h"
#include "aqua_tx_window.h"
#include "aqua_join.h"

#ifndef AQUA_DWELL_MS
#define AQUA_DWELL_MS        110    // listen time per channel
#endif
#ifndef AQUA_PROBE2_MS
#define AQUA_PROBE2_MS       50     // second probe on the same channel
#endif
#ifndef AQUA_BACKOFF_MIN_MS
#define AQUA_BACKOFF_MIN_MS  500
#endif
#ifndef AQUA_BACKOFF_MAX_MS
#define AQUA_BACKOFF_MAX_MS  8000    // longest pause between two full channel scans (a node must be back within ~60 s of a gateway restart)
#endif
#ifndef AQUA_LINK_TIMEOUT_MS
#define AQUA_LINK_TIMEOUT_MS 12000  // no ACK/NACK from the gateway for this long => rescan
#endif
#ifndef AQUA_HEARTBEAT_MS
#define AQUA_HEARTBEAT_MS    10000
#endif
#ifndef AQUA_TX_SPACING_MS
#define AQUA_TX_SPACING_MS   25     // min gap between frames (keeps ESP8266 TX queue safe)
#endif
#ifndef AQUA_ANNOUNCE_MS
#define AQUA_ANNOUNCE_MS     2000   // JOINING: PAIR_ANNOUNCE period
#endif
#ifndef AQUA_JOIN_PROBE_MS
#define AQUA_JOIN_PROBE_MS   4000   // JOINING: liveness probe period (detects a vanished gateway)
#endif
#ifndef AQUA_JOIN_TIMEOUT_MS
#define AQUA_JOIN_TIMEOUT_MS 30000  // JOINING that has not reached ONLINE after this long => start over (a gateway that answers probes but never challenges must not trap the node)
#endif
#ifndef AQUA_DROP_RESCAN
#define AQUA_DROP_RESCAN     3      // frames abandoned since last ACK that force a rescan
#endif

template <class HAL, uint8_t DEPTH, uint8_t MAXFRAME>
class AquaNodeLink {
 public:
  enum State : uint8_t { SCANNING = 0, ONLINE = 1, JOINING = 2 };

  struct Stats {
    uint32_t dataQueued, framesSent, retransmits, acks, nacks, heartbeats;
    uint32_t rediscoveries, scanRounds, rejectedFrames, sendFailures;
    uint32_t announces, pairings, pairFailures, unpaired;
  };

  // mac = this node's own radio MAC; kp = pairing key (aquaPairingKey); psk = per-site key (discovery and
  // handshake frames only).  Pointers must stay valid.
  void begin(HAL* hal, const uint8_t mac[6], uint8_t devType, const uint8_t* kp, const uint8_t* psk, uint8_t channelHint) {
    memcpy(mac_, mac, 6); devType_ = devType; kp_ = kp; psk_ = psk; hal_ = hal;
    joined_ = false; haveChal_ = false; joinEvent_ = false; firstJoinDone_ = false; unpaired_ = false;
    nodeId_ = 0; key_ = psk;
    bootId_ = hal_->random32() | 1u;
    seq_ = 0; hbSeq_ = 0;
    win_.reset(); memset(&stats_, 0, sizeof(stats_));
    state_ = SCANNING; lastChannel_ = channelHint; gwChannel_ = 0;
    backoffMs_ = AQUA_BACKOFF_MIN_MS; startScan(true);
    lastTxMs_ = 0; lastHbMs_ = 0; status_ = 0; rebootNeeded_ = false;
    lastAnnounceMs_ = 0; lastProbeMs_ = 0; everOnline_ = false;
  }

 public:

  // Queue a measurement.  Returns the assigned sequence number (0 on failure).
  uint32_t queueData(const void* payload, uint8_t len) {
    if (!joined_) return 0;                                        // no slot/key yet: pulses keep accumulating in the counter
    if (seq_ >= 0xFFFFFF00u) { rebootNeeded_ = true; return 0; }   // new boot session needed
    uint8_t buf[MAXFRAME];
    uint8_t flags = (uint8_t)(((state_ == ONLINE) ? 0 : AQUA_HF_BACKLOG) | (devType_ == AQUA_DEV_RAIN ? AQUA_HF_RAIN : 0));
    uint32_t s = ++seq_;
    size_t n = aquaBuildFrame(buf, AQUA_PKT_DATA, nodeId_, bootId_, s, flags, payload, len, key_);
    if (!win_.push(s, buf, (uint8_t)n)) return 0;
    stats_.dataQueued++;
    return s;
  }

  // Feed one received radio frame (call from loop context, not from an ISR).
  void onReceive(const uint8_t* src, const uint8_t* data, size_t len) {
    AquaHeader h; const uint8_t* p;
    if (aquaParseStruct(data, len, &h, &p) != AQUA_OK) { stats_.rejectedFrames++; return; }
    // nodeId 0 frames (discovery response, pairing handshake) are PSK-tagged; slot frames use the session key
    const uint8_t* k = (h.nodeId == 0) ? psk_ : key_;
    if ((h.nodeId != 0 && h.nodeId != nodeId_) || !aquaVerifyTag(data, len, k)) { stats_.rejectedFrames++; return; }
    uint32_t now = hal_->nowMs();
    if (h.type == AQUA_PKT_DISCOVERY_RESPONSE && (state_ == SCANNING || state_ == JOINING)) {
      AquaDiscoveryResponse r; memcpy(&r, p, sizeof(r));
      if (r.nonce != nonce_ || r.channel != curChannel_ || r.channel < AQUA_CH_MIN || r.channel > AQUA_CH_MAX) {
        stats_.rejectedFrames++; return;
      }
      if (state_ == JOINING) { lastGwRxMs_ = now; return; }          // liveness probe answered
      memcpy(gwMac_, r.gwMac, 6);
      gwChannel_ = r.channel; lastChannel_ = r.channel;
      hal_->setGateway(gwMac_);
      lastGwRxMs_ = now; backoffMs_ = AQUA_BACKOFF_MIN_MS;
      state_ = JOINING; haveChal_ = false; joinStartMs_ = now;           // must (re)prove identity before any data flows
      lastAnnounceMs_ = now - AQUA_ANNOUNCE_MS; lastProbeMs_ = now;
      return;
    }
    if (state_ == JOINING && h.nodeId == 0 && memcmp(src, gwMac_, 6) == 0) {
      if (h.type == AQUA_PKT_PAIR_CHALLENGE && h.bootId == bootId_) {
        AquaPairChallenge c; memcpy(&c, p, sizeof(c));
        if (memcmp(c.mac, mac_, 6) != 0) return;                       // challenge for another node
        memcpy(chal_, c.chal, 8); haveChal_ = true; lastGwRxMs_ = now;
        AquaPairResponse r; static const uint8_t zero8[8] = {0};
        aquaJoinMac(kp_, 'R', mac_, bootId_, chal_, 0, zero8, r.resp);
        uint8_t buf[MAXFRAME];
        size_t n = aquaBuildFrame(buf, AQUA_PKT_PAIR_RESPONSE, 0, bootId_, ++hbSeq_, 0, &r, sizeof(r), psk_);
        if (!hal_->sendUnicast(buf, n)) stats_.sendFailures++;
        return;
      }
      if (h.type == AQUA_PKT_PAIR_ACCEPT && h.bootId == bootId_ && haveChal_) {
        AquaPairAccept a; memcpy(&a, p, sizeof(a));
        uint8_t want[8];
        if (memcmp(a.mac, mac_, 6) != 0 || a.slot < 1 || a.slot > AQUA_MAX_SLOTS) { stats_.pairFailures++; return; }
        aquaJoinMac(kp_, 'A', mac_, bootId_, chal_, a.slot, a.salt, want);
        if (!aquaCtEq(want, a.proof, 8)) { stats_.pairFailures++; return; }     // the gateway does not know our pairing key
        if (joined_ && nodeId_ != 0 && nodeId_ != a.slot) { rebootNeeded_ = true; return; }   // slot changed: queued frames are stale
        uint8_t kn[16]; aquaNodeKey(kp_, a.salt, kn); aquaSessionKey(kn, bootId_, opKey_);
        memset(kn, 0, sizeof(kn));
        nodeId_ = a.slot; key_ = opKey_; joined_ = true; haveChal_ = false;
        if (everOnline_) stats_.rediscoveries++;
        everOnline_ = true; stats_.pairings++;
        if (!firstJoinDone_) { firstJoinDone_ = true; joinEvent_ = true; }
        state_ = ONLINE; lastGwRxMs_ = now; droppedAtAck_ = win_.dropped;
        win_.rearm();
        lastHbMs_ = now - AQUA_HEARTBEAT_MS;
      }
      return;
    }
    if (state_ != ONLINE || memcmp(src, gwMac_, 6) != 0) return;   // ACK/NACK only from the bound gateway
    if (h.type == AQUA_PKT_ACK && h.bootId == bootId_) {
      AquaAck a; memcpy(&a, p, sizeof(a));
      win_.onAck(a.cumAck, a.window);
      if (a.cfgPplX10 != cfgPpl_ || a.cfgFlags != cfgFlags_) { cfgPpl_ = a.cfgPplX10; cfgFlags_ = a.cfgFlags; cfgChanged_ = true; }
      lastGwRxMs_ = now; droppedAtAck_ = win_.dropped; stats_.acks++;
    } else if (h.type == AQUA_PKT_UNPAIR && h.bootId == bootId_) {
      unpaired_ = true; rebootNeeded_ = true; stats_.unpaired++;           // removed by the owner: restart as an unpaired node
    } else if (h.type == AQUA_PKT_NACK && h.bootId == bootId_) {
      AquaNack n; memcpy(&n, p, sizeof(n));
      for (uint8_t i = 0; i < n.count && i < AQUA_NACK_MAX; i++) win_.requestResend(n.seqs[i]);
      lastGwRxMs_ = now; stats_.nacks++;
    }
  }

  void tick() {
    uint32_t now = hal_->nowMs();
    if (state_ == SCANNING) { scanTick(now); return; }
    if (state_ == JOINING) { joinTick(now); return; }

    // link supervision
    if ((uint32_t)(now - lastGwRxMs_) > AQUA_LINK_TIMEOUT_MS ||
        (uint32_t)(win_.dropped - droppedAtAck_) >= AQUA_DROP_RESCAN) {
      state_ = SCANNING; hal_->clearGateway();
      backoffMs_ = AQUA_BACKOFF_MIN_MS; startScan(true);
      return;
    }
    if ((uint32_t)(now - lastTxMs_) < AQUA_TX_SPACING_MS) return;

    if ((uint32_t)(now - lastHbMs_) >= AQUA_HEARTBEAT_MS) {
      AquaHeartbeat hb; memset(&hb, 0, sizeof(hb));
      hb.uptimeS = now / 1000; hb.lastDataSeq = seq_; hb.status = status_; hb.channel = gwChannel_;
      hb.txRetransmits = (uint16_t)stats_.retransmits; hb.txDropped = (uint16_t)win_.dropped;
      hb.rediscoveries = (uint16_t)stats_.rediscoveries; hb.fwVersion = AQUA_FW_VERSION;
      uint8_t buf[MAXFRAME];
      size_t n = aquaBuildFrame(buf, AQUA_PKT_HEARTBEAT, nodeId_, bootId_, ++hbSeq_, devType_ == AQUA_DEV_RAIN ? AQUA_HF_RAIN : 0, &hb, sizeof(hb), key_);
      if (hal_->sendUnicast(buf, n)) { lastHbMs_ = now; lastTxMs_ = now; stats_.heartbeats++; }
      else stats_.sendFailures++;
      return;
    }
    int idx = win_.next(now);
    if (idx >= 0) {
      bool retry = win_.slots[idx].attempts > 0;
      if (hal_->sendUnicast(win_.slots[idx].frame, win_.slots[idx].len)) {
        win_.markSent(idx, now); lastTxMs_ = now; stats_.framesSent++;
        if (retry) stats_.retransmits++;
      } else {
        stats_.sendFailures++; lastTxMs_ = now;
      }
    }
  }

  State    state() const { return state_; }
  uint8_t  channel() const { return gwChannel_; }
  const uint8_t* gatewayMac() const { return gwMac_; }
  uint32_t bootId() const { return bootId_; }
  uint32_t lastSeq() const { return seq_; }
  uint8_t  pending() const { return win_.pending(); }
  const Stats& stats() const { return stats_; }
  uint32_t dropped() const { return win_.dropped; }
  uint32_t evicted() const { return win_.evicted; }
  bool     rebootRequested() const { return rebootNeeded_; }
  void     setStatus(uint16_t s) { status_ = s; }
  uint32_t lastGatewayRxMs() const { return lastGwRxMs_; }
  // true once after the gateway sent a different configuration (pulses/L x10, AQUA_CFG_* flags); the sketch stores it in NVS.
  bool     takeConfig(uint16_t* pplX10, uint8_t* flags) { if (!cfgChanged_) return false; *pplX10 = cfgPpl_; *flags = cfgFlags_; cfgChanged_ = false; return true; }
  bool     joined() const { return joined_; }
  bool     everJoined() const { return everOnline_; }
  bool     unpairedByGateway() const { return unpaired_; }
  uint8_t  slot() const { return nodeId_; }              // internal radio address - diagnostics only
  // true exactly once, right after the FIRST successful pairing of this boot (sketch resets its pulse baseline)
  bool     consumeJoinEvent() { bool e = joinEvent_; joinEvent_ = false; return e; }

 private:
  void startScan(bool fresh) {
    if (fresh) stats_.scanRounds++;
    // channel order: last known first, then ascending
    scanCount_ = 0;
    if (lastChannel_ >= AQUA_CH_MIN && lastChannel_ <= AQUA_CH_MAX) order_[scanCount_++] = lastChannel_;
    for (uint8_t c = AQUA_CH_MIN; c <= AQUA_CH_MAX; c++) if (c != lastChannel_) order_[scanCount_++] = c;
    scanIdx_ = 0; nonce_ = hal_->random32() | 1u;
    enterChannel_ = true; inBackoff_ = false;
  }

  void sendProbe(uint32_t now) {
    AquaDiscoveryRequest r; r.nonce = nonce_; r.probeChannel = curChannel_; r.fwVersion = AQUA_FW_VERSION;
    uint8_t buf[MAXFRAME];
    size_t n = aquaBuildFrame(buf, AQUA_PKT_DISCOVERY_REQUEST, 0, bootId_, ++hbSeq_, 0, &r, sizeof(r), psk_);
    if (!hal_->sendBroadcast(buf, n)) stats_.sendFailures++;
    (void)now;
  }

  // JOINING: announce ourselves; keep probing so a vanished gateway / changed channel is noticed.
  void joinTick(uint32_t now) {
    if ((uint32_t)(now - lastGwRxMs_) > AQUA_LINK_TIMEOUT_MS || (uint32_t)(now - joinStartMs_) > AQUA_JOIN_TIMEOUT_MS) {
      state_ = SCANNING; hal_->clearGateway(); backoffMs_ = AQUA_BACKOFF_MIN_MS; startScan(true);
      return;
    }
    if ((uint32_t)(now - lastTxMs_) < AQUA_TX_SPACING_MS) return;
    uint8_t buf[MAXFRAME];
    if ((uint32_t)(now - lastProbeMs_) >= AQUA_JOIN_PROBE_MS) {
      nonce_ = hal_->random32() | 1u; lastProbeMs_ = now; lastTxMs_ = now;
      sendProbe(now);
      return;
    }
    if ((uint32_t)(now - lastAnnounceMs_) >= AQUA_ANNOUNCE_MS) {
      AquaPairAnnounce a; a.devType = devType_; a.fwVersion = AQUA_FW_VERSION; a.channel = gwChannel_;
      size_t n = aquaBuildFrame(buf, AQUA_PKT_PAIR_ANNOUNCE, 0, bootId_, ++hbSeq_, 0, &a, sizeof(a), psk_);
      if (hal_->sendUnicast(buf, n)) { lastAnnounceMs_ = now; lastTxMs_ = now; stats_.announces++; }
      else stats_.sendFailures++;
    }
  }

  void scanTick(uint32_t now) {
    if (inBackoff_) {
      if ((int32_t)(now - nextRoundMs_) < 0) return;
      startScan(true);
    }
    if (enterChannel_) {
      curChannel_ = order_[scanIdx_];
      hal_->setChannel(curChannel_);
      chStartMs_ = now; probe2Sent_ = false; enterChannel_ = false;
      sendProbe(now);
      return;
    }
    if (!probe2Sent_ && (uint32_t)(now - chStartMs_) >= AQUA_PROBE2_MS) { sendProbe(now); probe2Sent_ = true; }
    if ((uint32_t)(now - chStartMs_) >= AQUA_DWELL_MS) {
      if (++scanIdx_ >= scanCount_) {
        inBackoff_ = true; nextRoundMs_ = now + backoffMs_;
        backoffMs_ = backoffMs_ * 2 > AQUA_BACKOFF_MAX_MS ? AQUA_BACKOFF_MAX_MS : backoffMs_ * 2;
      } else enterChannel_ = true;
    }
  }

  HAL* hal_ = nullptr;
  uint8_t nodeId_ = 0;
  const uint8_t* key_ = nullptr;
  uint32_t bootId_ = 0, seq_ = 0, hbSeq_ = 0;
  AquaTxWindow<DEPTH, MAXFRAME> win_;
  Stats stats_;
  State state_ = SCANNING;
  uint8_t gwMac_[6] = {0};
  uint8_t gwChannel_ = 0, lastChannel_ = 0, curChannel_ = 0;
  uint8_t order_[16];
  uint8_t scanCount_ = 0, scanIdx_ = 0;
  uint32_t nonce_ = 0, chStartMs_ = 0, nextRoundMs_ = 0, backoffMs_ = AQUA_BACKOFF_MIN_MS;
  bool enterChannel_ = true, inBackoff_ = false, probe2Sent_ = false, everOnline_ = false;
  uint32_t lastTxMs_ = 0, lastHbMs_ = 0, lastGwRxMs_ = 0, droppedAtAck_ = 0;
  uint16_t status_ = 0;
  bool rebootNeeded_ = false;
  uint16_t cfgPpl_ = 0; uint8_t cfgFlags_ = 0; bool cfgChanged_ = false;
  // identity / pairing
  bool joined_ = false, haveChal_ = false, joinEvent_ = false, firstJoinDone_ = false, unpaired_ = false;
  uint32_t lastAnnounceMs_ = 0, lastProbeMs_ = 0, joinStartMs_ = 0;
  uint8_t devType_ = AQUA_DEV_FLOW;
  uint8_t mac_[6] = {0};
  const uint8_t* kp_ = nullptr;
  const uint8_t* psk_ = nullptr;
  uint8_t opKey_[16] = {0};
  uint8_t chal_[8] = {0};
};
