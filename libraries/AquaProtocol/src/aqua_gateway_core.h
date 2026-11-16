/*
 * Gateway protocol core (pure logic, host-testable).
 *
 * Feed every received ESP-NOW frame to onFrame(); it validates, authenticates,
 * binds node MACs, tracks sequences and produces:
 *   - at most one reply frame (discovery response / handshake step / ACK)
 *   - at most one telemetry record to forward to MQTT
 * pollNack() produces retransmission requests for detected gaps.
 *
 * A gateway ACK means "received and validated by the gateway".  It does NOT
 * mean the record reached MQTT or SQLite.
 *
 * Registry (v2): the sketch fills the core from its NVS registry (registerNode).  A registered node has a
 * radio slot, a type, a MAC and the pairing key Kp derived from its pairing code.  It becomes usable after
 * the handshake (announce -> challenge -> response -> accept): then the core holds its per-boot session key
 * and verifies/acks its frames.  Unknown MACs are NEVER paired automatically; they are only reported as
 * "nodes nearby" (nearby table + rate-limited events) so the owner can pick one and type its code.
 */
#pragma once
#include <stdint.h>
#include <string.h>
#include "aqua_protocol.h"
#include "aqua_join.h"
#include "aqua_rx_tracker.h"

struct AquaTelemetry {
  uint8_t  nodeId;           // radio slot (internal)
  uint8_t  number;           // user-visible number within the type ("Node 2" / "Rain 1")
  uint8_t  devType;          // AquaDeviceType
  uint8_t  flags;            // bit0 recovered (arrived after a gap), bit1 backlog (node buffered it)
  uint8_t  payloadLen;
  uint8_t  channel;
  int8_t   rssi;
  uint8_t  mac[6];
  uint32_t bootId;
  uint32_t seq;
  uint32_t rxUptimeMs;       // gateway uptime when received
  uint32_t rxEpoch;          // unix seconds if NTP synced, else 0
  uint8_t  payload[sizeof(AquaFlowPayload)];
};
#define AQUA_TF_RECOVERED 0x01
#define AQUA_TF_BACKLOG   0x02

// Pairing events for the sketch to forward (see aqua_pair_text.h).
enum AquaPairEventKind : uint8_t { AQUA_PE_DISCOVERED = 1, AQUA_PE_PAIR_OK = 2, AQUA_PE_PAIR_FAIL = 3 };
struct AquaPairEvent {
  uint8_t  kind;
  uint8_t  mac[6];
  uint8_t  devType;
  uint16_t fwVersion;
  uint8_t  channel;
  int8_t   rssi;
  uint32_t bootId;
  uint8_t  slot;             // PAIR_OK / PAIR_FAIL: the registry slot
  bool     firstTime;        // PAIR_OK: the entry changed from pending to paired
};

struct AquaGwOut {
  uint8_t  frame[AQUA_MAX_FRAME];
  size_t   len;
  bool     broadcast;        // true: send to FF:FF:FF:FF:FF:FF
  uint8_t  toMac[6];
};

// One entry of the "nodes nearby waiting to pair" list.
struct AquaNearby {
  bool     used;
  uint8_t  mac[6];
  uint8_t  devType;
  uint16_t fwVersion;
  uint8_t  channel;
  int8_t   rssi;
  uint32_t lastSeenMs, firstSeenMs, announces;
};

class AquaGatewayCore {
 public:
  enum NodeState : uint8_t { NS_FREE = 0, NS_PENDING = 1, NS_PAIRED = 2 };
  static const uint8_t NEARBY_N = 8;
  static const uint32_t NEARBY_TTL_MS = 30000;      // forget a node that stopped announcing

  struct Node {
    uint8_t  state;          // NodeState
    uint8_t  number;         // display number within the type
    uint8_t  devType;
    uint8_t  mac[6];
    uint8_t  kp[16];         // pairing key (from the code)
    uint8_t  salt[8];        // chosen by the sketch at registration, stored in NVS, sent in ACCEPT
    AquaRxTracker rx;
    bool     everSeen;
    uint32_t lastSeenMs, lastSeenEpoch;
    uint32_t hbCount, invalid, discoveries;
    uint32_t nodeUptimeS;
    uint16_t nodeStatus, nodeRetransmits, nodeDropped, nodeRediscoveries;
    int8_t   rssi;
    bool     peerDirty;      // sketch must (re)register the ESP-NOW peer
    bool     keyed;          // a session key is installed (handshake succeeded in this node boot)
    uint32_t keyBoot;
    uint8_t  key[16];
    bool     chalPending;
    uint8_t  chal[8];
    uint32_t chalBoot, chalAtMs, lastChalMs;
    uint16_t cfgPplX10;      // calibration (pulses/L x10) pushed to the node in every ACK; 0 = leave the node's value
    uint8_t  cfgFlags;       // AQUA_CFG_* bits pushed to the node
  };

  struct Totals { uint32_t framesRx, rejected, badTag, badValue, unknownMac, discoveryRx, acksTx, nacksTx,
                  announces, pairOk, pairFail, nearbyEvents; };

  void begin(const uint8_t* psk, const uint8_t gwMac[6], uint32_t gwBootId) {
    psk_ = psk; memcpy(gwMac_, gwMac, 6); gwBootId_ = gwBootId;
    memset(nodes_, 0, sizeof(nodes_)); memset(&tot_, 0, sizeof(tot_));
    hbSeq_ = 0; rng_ = gwBootId | 1u; evHead_ = evTail_ = 0; memset(nearby_, 0, sizeof(nearby_)); memset(seen_, 0, sizeof(seen_));
  }

  Node& node(uint8_t slot) { return nodes_[slot - 1]; }
  const Node& node(uint8_t slot) const { return nodes_[slot - 1]; }
  static bool slotOk(uint8_t slot) { return slot >= 1 && slot <= AQUA_MAX_SLOTS; }
  int findByMac(const uint8_t mac[6]) const {
    for (uint8_t i = 1; i <= AQUA_MAX_SLOTS; i++) if (nodes_[i - 1].state != NS_FREE && memcmp(nodes_[i - 1].mac, mac, 6) == 0) return i;
    return -1;
  }

  // ---- registry operations (called by the sketch's registry layer) ----------------------------------
  // paired=false: a node the owner just added (waiting for its first handshake).  paired=true: restore from NVS
  // (the node must still handshake once per boot to get its session key).
  bool registerNode(uint8_t slot, uint8_t number, uint8_t devType, const uint8_t mac[6], const uint8_t kp[16],
                    const uint8_t salt[8], bool paired) {
    if (!slotOk(slot) || nodes_[slot - 1].state != NS_FREE || findByMac(mac) >= 0) return false;
    if (devType != AQUA_DEV_FLOW && devType != AQUA_DEV_RAIN) return false;
    Node& n = nodes_[slot - 1]; memset(&n, 0, sizeof(n));
    n.state = paired ? NS_PAIRED : NS_PENDING; n.number = number; n.devType = devType;
    memcpy(n.mac, mac, 6); memcpy(n.kp, kp, 16); memcpy(n.salt, salt, 8);
    n.peerDirty = true; n.rx.reset();
    dropNearby(mac);
    return true;
  }
  // Remove a node.  If it has a live session an UNPAIR frame is produced in *out (returns true) so the node
  // goes back to the unpaired state; the registry slot is freed either way.
  bool removeNode(uint8_t slot, AquaGwOut* out) {
    if (!slotOk(slot)) return false;
    Node& n = nodes_[slot - 1];
    bool frame = false;
    if (n.state != NS_FREE && n.keyed) frame = buildUnpair(n, slot, out);
    memset(&n, 0, sizeof(n));
    return frame;
  }
  // Replace the hardware behind a slot (keeps slot, number and therefore history): new MAC + new pairing key,
  // pending until the replacement completes the handshake.
  bool replaceNode(uint8_t slot, const uint8_t newMac[6], const uint8_t kp[16], const uint8_t salt[8]) {
    if (!slotOk(slot) || nodes_[slot - 1].state == NS_FREE) return false;
    int other = findByMac(newMac);
    if (other >= 0 && other != slot) return false;
    Node& n = nodes_[slot - 1];
    uint8_t number = n.number, type = n.devType;
    memset(&n, 0, sizeof(n));
    n.state = NS_PENDING; n.number = number; n.devType = type;
    memcpy(n.mac, newMac, 6); memcpy(n.kp, kp, 16); memcpy(n.salt, salt, 8); n.peerDirty = true; n.rx.reset();
    dropNearby(newMac);
    return true;
  }
  // Per-node configuration from the registry; the node receives it with its next ACK and stores it in NVS.
  bool setNodeConfig(uint8_t slot, uint16_t pplX10, uint8_t cfgFlags) {
    if (!slotOk(slot) || nodes_[slot - 1].state == NS_FREE) return false;
    nodes_[slot - 1].cfgPplX10 = pplX10; nodes_[slot - 1].cfgFlags = cfgFlags; return true;
  }
  uint8_t registeredCount() const { uint8_t c = 0; for (uint8_t i = 0; i < AQUA_MAX_SLOTS; i++) if (nodes_[i].state != NS_FREE) c++; return c; }

  bool popEvent(AquaPairEvent* ev) {
    if (evHead_ == evTail_) return false;
    *ev = events_[evTail_ % EV_Q]; evTail_++; return true;
  }
  const Totals& totals() const { return tot_; }

  // "Nodes nearby waiting to pair" (unknown MACs that announced within NEARBY_TTL_MS).
  uint8_t nearbyCount(uint32_t nowMs) const {
    uint8_t c = 0; for (uint8_t i = 0; i < NEARBY_N; i++) if (nearby_[i].used && (uint32_t)(nowMs - nearby_[i].lastSeenMs) <= NEARBY_TTL_MS) c++;
    return c;
  }
  bool nearbyAt(uint8_t idx, uint32_t nowMs, AquaNearby* out) const {      // idx counts only live entries
    uint8_t c = 0;
    for (uint8_t i = 0; i < NEARBY_N; i++) {
      if (!nearby_[i].used || (uint32_t)(nowMs - nearby_[i].lastSeenMs) > NEARBY_TTL_MS) continue;
      if (c++ == idx) { *out = nearby_[i]; return true; }
    }
    return false;
  }

  // Returns true when a reply is in *out; *telValid when *tel holds a record.
  bool onFrame(const uint8_t* src, const uint8_t* data, size_t len, uint32_t nowMs, uint32_t epoch,
               uint8_t channel, int8_t rssi, int8_t gwRssi, uint8_t gwFlags,
               AquaGwOut* out, AquaTelemetry* tel, bool* telValid) {
    *telValid = false; tot_.framesRx++;
    AquaHeader h; const uint8_t* p;
    AquaRc rc = aquaParseStruct(data, len, &h, &p);
    if (rc != AQUA_OK) { tot_.rejected++; return false; }
    // verification key: PSK for slot-less frames, the node's per-boot session key for slot frames
    const uint8_t* k = psk_;
    if (h.nodeId != 0) {
      const Node& d = nodes_[h.nodeId - 1];
      if (d.state == NS_FREE || !d.keyed) { tot_.unknownMac++; tot_.rejected++; return false; }
      k = d.key;
    }
    if (!aquaVerifyTag(data, len, k)) { tot_.rejected++; tot_.badTag++; return false; }

    if (h.nodeId == 0) {                                   // slot-less frames: discovery + pairing handshake
      if (h.type == AQUA_PKT_DISCOVERY_REQUEST) {
        tot_.discoveryRx++;
        AquaDiscoveryRequest rq; memcpy(&rq, p, sizeof(rq));
        AquaDiscoveryResponse r; memset(&r, 0, sizeof(r));
        r.nonce = rq.nonce; r.channel = channel; memcpy(r.gwMac, gwMac_, 6);
        r.gwBootId = gwBootId_; r.gwRssi = gwRssi; r.gwFlags = gwFlags;
        out->len = aquaBuildFrame(out->frame, AQUA_PKT_DISCOVERY_RESPONSE, 0, h.bootId, ++hbSeq_, 0, &r, sizeof(r), psk_);
        out->broadcast = true; memset(out->toMac, 0xFF, 6);
        return true;
      }
      if (h.type == AQUA_PKT_PAIR_ANNOUNCE) return onAnnounce(src, h, p, nowMs, channel, rssi, out);
      if (h.type == AQUA_PKT_PAIR_RESPONSE) return onPairResponse(src, h, p, nowMs, epoch, channel, rssi, out);
      return false;
    }

    Node& n = node(h.nodeId);
    if (memcmp(n.mac, src, 6) != 0) { tot_.unknownMac++; return false; }       // slot frames only from the registered MAC
    n.everSeen = true; n.lastSeenMs = nowMs; n.lastSeenEpoch = epoch; n.rssi = rssi;

    if (h.type == AQUA_PKT_HEARTBEAT) {
      AquaHeartbeat hb; memcpy(&hb, p, sizeof(hb));
      n.hbCount++; n.nodeUptimeS = hb.uptimeS; n.nodeStatus = hb.status;
      n.nodeRetransmits = hb.txRetransmits; n.nodeDropped = hb.txDropped; n.nodeRediscoveries = hb.rediscoveries;
      n.rx.onHeartbeat(h.bootId, hb.lastDataSeq);
      return makeAck(n, h, 0, AQUA_PKT_HEARTBEAT, gwFlags, out);
    }

    if (h.type == AQUA_PKT_DATA) {
      bool isRain = (h.flags & AQUA_HF_RAIN) != 0;
      bool valid = isRain == (n.devType == AQUA_DEV_RAIN);                 // payload kind must match the registered type
      if (valid) {
        if (isRain) { AquaRainPayload rp; memcpy(&rp, p, sizeof(rp)); valid = aquaValidateRain(rp) == AQUA_OK; }
        else { AquaFlowPayload fp; memcpy(&fp, p, sizeof(fp)); valid = aquaValidateFlow(fp) == AQUA_OK; }
      }
      bool newSession;
      AquaRxResult res = n.rx.onData(h.bootId, h.seq, &newSession);
      if (res == AQUA_RX_STALE_SESSION) return false;            // old-boot straggler: ignore, no ACK
      if (!valid) { n.invalid++; tot_.badValue++; }
      else if (res != AQUA_RX_DUPLICATE) {
        memset(tel, 0, sizeof(*tel));
        tel->nodeId = h.nodeId; tel->number = n.number; tel->devType = n.devType; memcpy(tel->mac, n.mac, 6);
        tel->bootId = h.bootId; tel->seq = h.seq; tel->payloadLen = h.payloadLen;
        tel->channel = channel; tel->rssi = rssi; tel->rxUptimeMs = nowMs; tel->rxEpoch = epoch;
        tel->flags = (res == AQUA_RX_RECOVERED ? AQUA_TF_RECOVERED : 0) | ((h.flags & AQUA_HF_BACKLOG) ? AQUA_TF_BACKLOG : 0);
        memcpy(tel->payload, p, h.payloadLen);
        *telValid = true;
      }
      return makeAck(n, h, h.seq, AQUA_PKT_DATA, gwFlags, out);    // ACK duplicates too, so the node stops resending
    }
    return false;                                                   // ACK/NACK/UNPAIR are never expected from a node
  }

  bool pollNack(uint8_t slot, uint32_t nowMs, AquaGwOut* out) {
    Node& n = node(slot);
    if (n.state == NS_FREE || !n.keyed) return false;
    AquaNack nk;
    if (!n.rx.poll(nowMs, &nk)) return false;
    out->len = aquaBuildFrame(out->frame, AQUA_PKT_NACK, slot, n.rx.bootId, ++hbSeq_, 0, &nk, sizeof(nk), n.key);
    out->broadcast = false; memcpy(out->toMac, n.mac, 6);
    tot_.nacksTx++;
    return true;
  }

 private:
  uint32_t rand32() { rng_ ^= rng_ << 13; rng_ ^= rng_ >> 17; rng_ ^= rng_ << 5; return rng_; }

  void pushEvent(const AquaPairEvent& e) {
    if ((uint8_t)(evHead_ - evTail_) >= EV_Q) evTail_++;                 // drop oldest, never block
    events_[evHead_ % EV_Q] = e; evHead_++;
  }

  void dropNearby(const uint8_t mac[6]) {
    for (uint8_t i = 0; i < NEARBY_N; i++) if (nearby_[i].used && memcmp(nearby_[i].mac, mac, 6) == 0) nearby_[i].used = false;
  }

  bool buildUnpair(Node& n, uint8_t slot, AquaGwOut* out) {
    AquaUnpair u; u.reason = 1;
    out->len = aquaBuildFrame(out->frame, AQUA_PKT_UNPAIR, slot, n.keyBoot, ++hbSeq_, 0, &u, sizeof(u), n.key);
    out->broadcast = false; memcpy(out->toMac, n.mac, 6);
    return true;
  }

  // PAIR_ANNOUNCE: registered MAC -> challenge it; unknown MAC -> list it as "nearby" (never auto-pair).
  bool onAnnounce(const uint8_t* src, const AquaHeader& h, const uint8_t* p, uint32_t nowMs, uint8_t channel,
                  int8_t rssi, AquaGwOut* out) {
    tot_.announces++;
    AquaPairAnnounce a; memcpy(&a, p, sizeof(a));
    int slot = findByMac(src);
    if (slot > 0) {
      Node& n = nodes_[slot - 1];
      if (a.devType != n.devType) return false;                          // registered as another type: refuse
      if (n.chalPending && (uint32_t)(nowMs - n.lastChalMs) < 1000UL) return false;   // rate-limit online guessing
      uint32_t r0 = rand32(), r1 = rand32();
      memcpy(n.chal, &r0, 4); memcpy(n.chal + 4, &r1, 4);
      n.chalPending = true; n.chalBoot = h.bootId; n.chalAtMs = nowMs; n.lastChalMs = nowMs;
      AquaPairChallenge c; memcpy(c.mac, src, 6); memcpy(c.chal, n.chal, 8);
      out->len = aquaBuildFrame(out->frame, AQUA_PKT_PAIR_CHALLENGE, 0, h.bootId, ++hbSeq_, 0, &c, sizeof(c), psk_);
      out->broadcast = false; memcpy(out->toMac, src, 6);
      return true;
    }
    // unknown MAC: update the nearby table, emit a rate-limited event (at most once per 10 s per MAC)
    AquaNearby* e = nullptr; AquaNearby* victim = nullptr;
    for (uint8_t i = 0; i < NEARBY_N; i++) {
      if (nearby_[i].used && memcmp(nearby_[i].mac, src, 6) == 0) { e = &nearby_[i]; break; }
      if (!victim || (victim->used && (!nearby_[i].used || nearby_[i].lastSeenMs < victim->lastSeenMs))) victim = &nearby_[i];
    }
    if (!e) { e = victim; memset(e, 0, sizeof(*e)); e->used = true; memcpy(e->mac, src, 6); e->firstSeenMs = nowMs; }
    e->devType = a.devType; e->fwVersion = a.fwVersion; e->channel = channel; e->rssi = rssi; e->lastSeenMs = nowMs; e->announces++;
    int si = -1; bool recent = false;
    for (uint8_t i = 0; i < SEEN_N; i++) {
      if (seen_[i].used && memcmp(seen_[i].mac, src, 6) == 0) { si = i; recent = (uint32_t)(nowMs - seen_[i].atMs) < 10000UL; break; }
    }
    if (si < 0) {
      si = 0;
      for (uint8_t i = 0; i < SEEN_N; i++) {
        if (!seen_[i].used) { si = i; break; }
        if (seen_[i].atMs < seen_[si].atMs) si = i;
      }
    }
    if (recent) return false;
    seen_[si].used = true; memcpy(seen_[si].mac, src, 6); seen_[si].atMs = nowMs;
    AquaPairEvent ev; memset(&ev, 0, sizeof(ev));
    ev.kind = AQUA_PE_DISCOVERED; memcpy(ev.mac, src, 6); ev.devType = a.devType; ev.fwVersion = a.fwVersion;
    ev.channel = channel; ev.rssi = rssi; ev.bootId = h.bootId;
    pushEvent(ev); tot_.nearbyEvents++;
    return false;
  }

  // PAIR_RESPONSE: verify MAC_Kp(challenge), install the session key, answer with the mutual proof.
  bool onPairResponse(const uint8_t* src, const AquaHeader& h, const uint8_t* p, uint32_t nowMs, uint32_t epoch,
                      uint8_t channel, int8_t rssi, AquaGwOut* out) {
    int slot = findByMac(src);
    if (slot < 0) return false;
    Node& n = nodes_[slot - 1];
    if (!n.chalPending || n.chalBoot != h.bootId || (uint32_t)(nowMs - n.chalAtMs) > 10000UL) return false;
    AquaPairResponse r; memcpy(&r, p, sizeof(r));
    static const uint8_t zero8[8] = {0};
    uint8_t want[8]; aquaJoinMac(n.kp, 'R', n.mac, h.bootId, n.chal, 0, zero8, want);
    AquaPairEvent e; memset(&e, 0, sizeof(e));
    memcpy(e.mac, src, 6); e.devType = n.devType; e.channel = channel; e.rssi = rssi; e.bootId = h.bootId; e.slot = (uint8_t)slot;
    n.chalPending = false;
    if (!aquaCtEq(want, r.resp, 8)) { tot_.pairFail++; e.kind = AQUA_PE_PAIR_FAIL; pushEvent(e); return false; }
    uint8_t kn[16]; aquaNodeKey(n.kp, n.salt, kn); aquaSessionKey(kn, h.bootId, n.key); memset(kn, 0, sizeof(kn));
    n.keyed = true; n.keyBoot = h.bootId;
    e.firstTime = n.state == NS_PENDING; n.state = NS_PAIRED;
    n.everSeen = true; n.lastSeenMs = nowMs; n.lastSeenEpoch = epoch; n.rssi = rssi;
    AquaPairAccept acc; memcpy(acc.mac, n.mac, 6); acc.slot = (uint8_t)slot; memcpy(acc.salt, n.salt, 8);
    aquaJoinMac(n.kp, 'A', n.mac, h.bootId, n.chal, (uint8_t)slot, n.salt, acc.proof);
    out->len = aquaBuildFrame(out->frame, AQUA_PKT_PAIR_ACCEPT, 0, h.bootId, ++hbSeq_, 0, &acc, sizeof(acc), psk_);
    out->broadcast = false; memcpy(out->toMac, n.mac, 6);
    tot_.pairOk++; e.kind = AQUA_PE_PAIR_OK; pushEvent(e);
    return true;
  }

  bool makeAck(Node& n, const AquaHeader& h, uint32_t ackedSeq, uint8_t ackedType, uint8_t gwFlags, AquaGwOut* out) {
    AquaAck a; memset(&a, 0, sizeof(a));
    a.ackedSeq = ackedSeq; a.cumAck = n.rx.cumAck; a.window = n.rx.window; a.ackedType = ackedType; a.gwFlags = gwFlags;
    a.cfgPplX10 = n.cfgPplX10; a.cfgFlags = n.cfgFlags;
    // a heartbeat ACK for a session the tracker does not know yet must not claim a cumulative position
    if (!n.rx.active || n.rx.bootId != h.bootId) { a.cumAck = 0; a.window = 0; }
    out->len = aquaBuildFrame(out->frame, AQUA_PKT_ACK, h.nodeId, h.bootId, ++hbSeq_, 0, &a, sizeof(a), n.key);
    out->broadcast = false; memcpy(out->toMac, n.mac, 6);
    tot_.acksTx++;
    return true;
  }

  const uint8_t* psk_ = nullptr;
  uint8_t gwMac_[6] = {0};
  uint32_t gwBootId_ = 0, hbSeq_ = 0;
  Node nodes_[AQUA_MAX_SLOTS];
  Totals tot_;
  uint32_t rng_ = 1;
  static const uint8_t EV_Q = 6, SEEN_N = 8;
  AquaPairEvent events_[EV_Q];
  uint8_t evHead_ = 0, evTail_ = 0;
  AquaNearby nearby_[NEARBY_N];
  struct Seen { bool used; uint8_t mac[6]; uint32_t atMs; } seen_[SEEN_N];
};
