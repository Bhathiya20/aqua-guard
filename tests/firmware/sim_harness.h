// Virtual-time ESP-NOW network simulation shared by the unit tests and the integration exporter.
// Models: flow nodes <-> gateway core (registry + pairing handshake), with loss / duplication / reordering,
// channel changes, gateway outages and node reboots.  Uses the REAL library code (no re-implementation).
#pragma once
#include <vector>
#include <algorithm>
#include <cstdio>
#include "AquaProtocol.h"

static const uint8_t KEY[16] = {1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16};     // site PSK (test value)
static const uint8_t GWMAC[6] = {0xF0,0x24,0xF9,0x0D,0xB3,0xB4};
static uint32_t g_epochBase = 1700000000u;   // unix seconds at sim t=0 (NTP time at the gateway)
static const char* TEST_CODES[3] = {"ABCD-EFGH-JKMN", "PQRS-TVWX-YZ01", "2345-6789-ABCD"};

static void testMac(int i, uint8_t m[6]) { uint8_t b[6] = {0x34, 0x85, 0x18, 0, 0, (uint8_t)(i + 1)}; memcpy(m, b, 6); }
static void testKp(int i, uint8_t kp[16]) {
  char code[AQUA_CODE_LEN + 1]; uint8_t mac[6];
  aquaCodeNormalize(TEST_CODES[i], code); testMac(i, mac); aquaPairingKey(code, mac, kp);
}
static void testSalt(int i, uint8_t salt[8]) { for (int k = 0; k < 8; k++) salt[k] = (uint8_t)(0x40 + i * 16 + k); }

// ---------------------------------------------------------------- simulation
struct SimEvent { uint32_t at; int to; /* 0 gateway, 1..3 node */ uint8_t ch; uint8_t src[6]; std::vector<uint8_t> data; };

struct SimNet {
  uint32_t now = 0; uint32_t rng = 12345;
  uint8_t gwChannel = 6;
  bool gwUp = true;
  double loss = 0.0, dup = 0.0, reorder = 0.0;
  std::vector<SimEvent> q;
  uint32_t rnd() { rng = rng * 1664525u + 1013904223u; return rng >> 8; }
  double frand() { return (rnd() & 0xFFFF) / 65536.0; }
};

struct SimNodeHal {
  SimNet* net; int id; uint8_t mac[6]; uint8_t ch = 1; bool hasGw = false; uint32_t rngState;
  uint32_t nowMs() { return net->now; }
  uint32_t random32() { rngState = rngState * 22695477u + 1u; return rngState; }
  void setChannel(uint8_t c) { ch = c; }
  void setGateway(const uint8_t*) { hasGw = true; }
  void clearGateway() { hasGw = false; }
  void post(const uint8_t* d, size_t n, int to) {
    if (net->frand() < net->loss) return;
    SimEvent e; e.at = net->now + 1 + (net->rnd() % 4); e.to = to; e.ch = ch; memcpy(e.src, mac, 6); e.data.assign(d, d + n);
    if (net->frand() < net->reorder) e.at += 40 + net->rnd() % 200;
    net->q.push_back(e);
    if (net->frand() < net->dup) { e.at += 1 + net->rnd() % 30; net->q.push_back(e); }
  }
  bool sendBroadcast(const uint8_t* d, size_t n) { post(d, n, 0); return true; }
  bool sendUnicast(const uint8_t* d, size_t n) { if (!hasGw) return false; post(d, n, 0); return true; }
};

typedef AquaNodeLink<SimNodeHal, 24, 64> SimLink;

struct SimNode {
  SimNodeHal hal; SimLink link; AquaFlowMeter meter; uint32_t pulses = 0; uint32_t nextSample = 2000;
  double lpm = 6.0; double pulseAcc = 0; bool powered = true;
  uint32_t trueTotalPulses = 0;   // ground truth since the node joined (the volume record starts at the join)
  uint32_t base = 0;              // ISR counter value at the first join of this boot
  uint8_t kp[16];
};

struct SimGateway {
  AquaGatewayCore core; uint8_t channel = 6;
  std::vector<AquaTelemetry> published;
  std::vector<AquaPairEvent> events;
};

// Runs the full node <-> gateway loop in virtual time.
struct Sim {
  SimNet net; SimNode nodes[3]; SimGateway gw;
  // The registry layer: what the gateway sketch does after adding a node / restoring NVS.
  bool registerNode(int i, bool paired = false) {
    uint8_t mac[6], kp[16], salt[8]; testMac(i, mac); testKp(i, kp); testSalt(i, salt);
    return gw.core.registerNode((uint8_t)(i + 1), (uint8_t)(i + 1), AQUA_DEV_FLOW, mac, kp, salt, paired);
  }
  void bootNode(int i, uint32_t seed, int codeIndex = -1) {
    SimNode& n = nodes[i];
    n.hal.net = &net; n.hal.id = i + 1; n.hal.rngState = seed;
    testMac(i, n.hal.mac); testKp(codeIndex < 0 ? i : codeIndex, n.kp);
    n.hal.hasGw = false; n.hal.ch = 1;
    n.link.begin(&n.hal, n.hal.mac, AQUA_DEV_FLOW, n.kp, KEY, 0);
    n.pulses = 0; n.base = 0; n.meter.begin(450.0f, net.now, 0); n.nextSample = net.now + 2000; n.pulseAcc = 0;
    n.trueTotalPulses = 0; n.powered = true; n.lpm = 6.0;
  }
  void begin() {
    net.now = 1000;
    gw.core.begin(KEY, GWMAC, 0x77);
    registerNode(0); registerNode(1);
    bootNode(0, 111); bootNode(1, 222);
    nodes[2].powered = false;
  }
  void gwSend(const AquaGwOut& o) {
    for (int i = 0; i < 3; i++) {
      SimNode& n = nodes[i];
      if (!n.powered) continue;
      if (!o.broadcast && memcmp(o.toMac, n.hal.mac, 6) != 0) continue;
      if (net.frand() < net.loss) continue;
      SimEvent e; e.at = net.now + 1 + (net.rnd() % 4); e.to = i + 1; e.ch = gw.channel; memcpy(e.src, GWMAC, 6);
      e.data.assign(o.frame, o.frame + o.len);
      if (net.frand() < net.reorder) e.at += 40 + net.rnd() % 200;
      net.q.push_back(e);
    }
  }
  // advance one millisecond
  void step() {
    net.now++;
    // deliver due events
    for (size_t k = 0; k < net.q.size();) {
      if (net.q[k].at <= net.now) {
        SimEvent e = net.q[k]; net.q.erase(net.q.begin() + k);
        if (e.to == 0) {
          if (!net.gwUp || e.ch != gw.channel) continue;               // radio on a different channel: never heard
          AquaGwOut out; AquaTelemetry tel; bool tv;
          if (gw.core.onFrame(e.src, e.data.data(), e.data.size(), net.now, g_epochBase + net.now / 1000,
                              gw.channel, -50, -40, 3, &out, &tel, &tv)) gwSend(out);
          if (tv) gw.published.push_back(tel);
          AquaPairEvent pe; while (gw.core.popEvent(&pe)) gw.events.push_back(pe);
        } else {
          SimNode& n = nodes[e.to - 1];
          if (n.powered && e.ch == n.hal.ch) n.link.onReceive(e.src, e.data.data(), e.data.size());
        }
      } else k++;
    }
    for (int i = 0; i < 3; i++) {
      SimNode& n = nodes[i];
      if (!n.powered) continue;
      // pulses: continuous, independent of the radio state
      n.pulseAcc += n.lpm * 450.0 / 60000.0;                             // pulses per ms
      while (n.pulseAcc >= 1.0) { n.pulseAcc -= 1.0; n.pulses++; n.trueTotalPulses++; }
      if (net.now >= n.nextSample) {
        if (n.link.consumeJoinEvent()) { n.base = n.pulses; n.trueTotalPulses = 0; n.meter.begin(450.0f, net.now, 0); }   // volume counts from the join
        AquaFlowPayload p; n.meter.compute(net.now, n.pulses - n.base, false, net.now / 1000, &p);
        p.batteryMv = (uint16_t)(7400 - i * 100);
        n.nextSample += 2000;
        n.link.queueData(&p, sizeof(p));
      }
      n.link.tick();
    }
    // NACK polling
    for (uint8_t id = 1; id <= AQUA_MAX_SLOTS; id++) { AquaGwOut o; if (net.gwUp && gw.core.pollNack(id, net.now, &o)) gwSend(o); }
  }
  void run(uint32_t ms) { for (uint32_t i = 0; i < ms; i++) step(); }
  uint32_t distinctSeq(int node, uint32_t boot) {
    std::vector<uint32_t> s;
    for (auto& t : gw.published) if (t.number == node && t.bootId == boot) s.push_back(t.seq);
    std::sort(s.begin(), s.end()); s.erase(std::unique(s.begin(), s.end()), s.end());
    return (uint32_t)s.size();
  }
  // Volume reconstructed by the backend rule: last cumPulses per boot (mod 2^32).
  uint32_t reconstructedPulses(int node, uint32_t boot) {
    uint32_t best = 0; uint32_t bestSeq = 0;
    for (auto& t : gw.published) if (t.number == node && t.bootId == boot && t.seq >= bestSeq) {
      AquaFlowPayload f; memcpy(&f, t.payload, sizeof(f)); best = f.cumPulses; bestSeq = t.seq; }
    return best;
  }
};

// A hand-driven node for protocol-level tests: performs the handshake frame by frame against a bare core.
struct ManualNode {
  uint8_t mac[6]; uint8_t kp[16]; uint32_t boot = 0xBEEF0001u; uint8_t devType = AQUA_DEV_FLOW;
  uint8_t key[16]; uint8_t slot = 0; bool joined = false; uint32_t seq = 0;
  uint8_t chal[8];

  size_t announce(uint8_t* buf) {
    AquaPairAnnounce a; a.devType = devType; a.channel = 6; a.fwVersion = AQUA_FW_VERSION;
    return aquaBuildFrame(buf, AQUA_PKT_PAIR_ANNOUNCE, 0, boot, 1, 0, &a, sizeof(a), KEY);
  }
  // Runs announce -> challenge -> response -> accept.  badKp: answer with a wrong pairing key.  Returns true if the
  // gateway accepted AND the node verified the gateway's proof.
  bool join(AquaGatewayCore& core, uint32_t& now, bool badKp = false) {
    uint8_t buf[AQUA_MAX_FRAME]; AquaGwOut out; AquaTelemetry tel; bool tv;
    now += 1500;
    size_t n = announce(buf);
    if (!core.onFrame(mac, buf, n, now, 0, 6, -50, -40, 3, &out, &tel, &tv)) return false;
    AquaHeader h; const uint8_t* p;
    if (aquaParseFrame(out.frame, out.len, KEY, &h, &p) != AQUA_OK || h.type != AQUA_PKT_PAIR_CHALLENGE) return false;
    AquaPairChallenge c; memcpy(&c, p, sizeof(c));
    if (memcmp(c.mac, mac, 6) != 0) return false;
    memcpy(chal, c.chal, 8);
    uint8_t usedKp[16]; memcpy(usedKp, kp, 16); if (badKp) usedKp[0] ^= 0x55;
    AquaPairResponse r; static const uint8_t zero8[8] = {0};
    aquaJoinMac(usedKp, 'R', mac, boot, chal, 0, zero8, r.resp);
    n = aquaBuildFrame(buf, AQUA_PKT_PAIR_RESPONSE, 0, boot, 2, 0, &r, sizeof(r), KEY);
    now += 20;
    if (!core.onFrame(mac, buf, n, now, 0, 6, -50, -40, 3, &out, &tel, &tv)) return false;
    if (aquaParseFrame(out.frame, out.len, KEY, &h, &p) != AQUA_OK || h.type != AQUA_PKT_PAIR_ACCEPT) return false;
    AquaPairAccept a; memcpy(&a, p, sizeof(a));
    uint8_t want[8]; aquaJoinMac(kp, 'A', mac, boot, chal, a.slot, a.salt, want);
    if (!aquaCtEq(want, a.proof, 8)) return false;                       // gateway failed to prove it knows Kp
    uint8_t kn[16]; aquaNodeKey(kp, a.salt, kn); aquaSessionKey(kn, boot, key);
    slot = a.slot; joined = true; seq = 0;
    return true;
  }
  size_t data(uint8_t* buf, const AquaFlowPayload& f, const uint8_t* useKey = nullptr) {
    return aquaBuildFrame(buf, AQUA_PKT_DATA, slot, boot, ++seq, 0, &f, sizeof(f), useKey ? useKey : key);
  }
};
