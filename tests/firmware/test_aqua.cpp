// Host-side tests for the Aqua Guard protocol library (compile with clang++/g++).
// Build + run: tests/firmware/run_tests.sh   (or see README "Testing")
#include <cstdio>
#include <cstdlib>
#include <vector>
#include <deque>
#include <algorithm>
#include <string>
#include "AquaProtocol.h"
#include "sim_harness.h"

static int g_fail = 0, g_pass = 0;
#define CHECK(c) do { if (c) { g_pass++; } else { g_fail++; printf("  FAIL %s:%d  %s\n", __FILE__, __LINE__, #c); } } while (0)
#define SECTION(n) printf("[%s]\n", n)


// ---------------------------------------------------------------- protocol
static void test_sizes_and_siphash() {
  SECTION("sizes + siphash");
  CHECK(sizeof(AquaHeader) == 14);
  CHECK(sizeof(AquaFlowPayload) == 34);
  CHECK(sizeof(AquaRainPayload) == 18);
  CHECK(sizeof(AquaAck) == 17);
  CHECK(sizeof(AquaNack) == 25);
  CHECK(sizeof(AquaHeartbeat) == 19);
  uint8_t k[16], m[16];
  for (int i = 0; i < 16; i++) { k[i] = (uint8_t)i; m[i] = (uint8_t)i; }
  // Reference vectors from the SipHash paper / reference implementation
  CHECK(aquaSipHash24(k, m, 0)  == 0x726fdb47dd0e0e31ULL);
  CHECK(aquaSipHash24(k, m, 15) == 0xa129ca6149be45e5ULL);
}

static void test_build_parse_reject() {
  SECTION("frame build/parse/reject");
  AquaFlowPayload f; memset(&f, 0, sizeof(f));
  f.intervalPulses = 450; f.cumPulses = 450; f.flowMlPerMin = 30000; f.intervalMl = 1000; f.totalMl = 1000;
  f.intervalMs = 2000; f.pulsesPerLiterX10 = 4500;
  uint8_t buf[AQUA_MAX_FRAME];
  size_t n = aquaBuildFrame(buf, AQUA_PKT_DATA, 1, 0xABCD1234, 7, 0, &f, sizeof(f), KEY);
  CHECK(n == 14 + 34 + 8);
  AquaHeader h; const uint8_t* p;
  CHECK(aquaParseFrame(buf, n, KEY, &h, &p) == AQUA_OK);
  CHECK(h.seq == 7 && h.bootId == 0xABCD1234 && h.nodeId == 1);
  // wrong key
  uint8_t bad[16]; memcpy(bad, KEY, 16); bad[0] ^= 1;
  CHECK(aquaParseFrame(buf, n, bad, &h, &p) == AQUA_E_TAG);
  // corrupt payload bit
  uint8_t c[AQUA_MAX_FRAME]; memcpy(c, buf, n); c[20] ^= 0x10;
  CHECK(aquaParseFrame(c, n, KEY, &h, &p) == AQUA_E_TAG);
  // truncated
  CHECK(aquaParseFrame(buf, n - 1, KEY, &h, &p) == AQUA_E_LEN);
  CHECK(aquaParseFrame(buf, 5, KEY, &h, &p) == AQUA_E_LEN);
  // wrong magic / version / type / node
  memcpy(c, buf, n); c[0] = 0; CHECK(aquaParseFrame(c, n, KEY, &h, &p) == AQUA_E_MAGIC);
  memcpy(c, buf, n); c[1] = 1; CHECK(aquaParseFrame(c, n, KEY, &h, &p) == AQUA_E_VERSION);      // v1 frames are refused
  memcpy(c, buf, n); c[2] = 99; CHECK(aquaParseFrame(c, n, KEY, &h, &p) == AQUA_E_TYPE);
  memcpy(c, buf, n); c[3] = AQUA_MAX_SLOTS + 1; CHECK(aquaParseFrame(c, n, KEY, &h, &p) == AQUA_E_NODE);
  // payload length mismatch (valid tag, wrong type for node): rain node with flow-size payload
  size_t n2 = aquaBuildFrame(c, AQUA_PKT_DATA, 3, 1, 1, AQUA_HF_RAIN, &f, sizeof(f), KEY);
  CHECK(aquaParseFrame(c, n2, KEY, &h, &p) == AQUA_E_PLEN);
  // measurement validation
  AquaFlowPayload v = f; CHECK(aquaValidateFlow(v) == AQUA_OK);
  v = f; v.flowMlPerMin = 500000; CHECK(aquaValidateFlow(v) == AQUA_E_VALUE);
  v = f; v.intervalMs = 0; CHECK(aquaValidateFlow(v) == AQUA_E_VALUE);
  v = f; v.pulsesPerLiterX10 = 0; CHECK(aquaValidateFlow(v) == AQUA_E_VALUE);
  v = f; v.batteryMv = 30000; CHECK(aquaValidateFlow(v) == AQUA_E_VALUE);
  v = f; v.batteryMv = AQUA_BATT_NONE; CHECK(aquaValidateFlow(v) == AQUA_OK);
  AquaRainPayload r; memset(&r, 0, sizeof(r)); r.levelX10 = 1200; CHECK(aquaValidateRain(r) == AQUA_E_VALUE);
  r.levelX10 = 0xFFFF; r.distanceMm = 0xFFFF; CHECK(aquaValidateRain(r) == AQUA_OK);   // invalid markers are legal
  r.turbidityAdc = 5000; CHECK(aquaValidateRain(r) == AQUA_E_VALUE);
}

// ---------------------------------------------------------------- flow math
static void test_flow_meter() {
  SECTION("flow meter");
  AquaFlowMeter m; AquaFlowPayload o;
  m.begin(450.0f, 0, 0);
  m.compute(2000, 450, false, 2, &o);
  CHECK(o.intervalPulses == 450 && o.intervalMl == 1000 && o.flowMlPerMin == 30000 && o.cumPulses == 450);
  m.compute(4000, 1350, false, 4, &o);                 // +900 pulses = 2 L in 2 s
  CHECK(o.intervalMl == 2000 && o.flowMlPerMin == 60000 && o.totalMl == 3000);
  CHECK(o.status & AQUA_FS_OVER_RANGE);
  m.compute(6000, 1575, false, 6, &o);                 // +225 = 0.5 L
  CHECK(o.intervalMl == 500 && o.flowMlPerMin == 15000);
  CHECK(!(o.status & AQUA_FS_OVER_RANGE));
  // actual elapsed time (not assumed 2 s): 225 pulses over 3000 ms = 10 L/min
  m.compute(9000, 1800, false, 9, &o);
  CHECK(o.flowMlPerMin == 10000 && (o.status == 0));
  // 450 pulses over 800 ms = 75 L/min (and a short interval is flagged as jitter)
  m.compute(9800, 2250, false, 10, &o);
  CHECK(o.intervalPulses == 450 && o.flowMlPerMin == 75000 && (o.status & AQUA_FS_INTERVAL_JIT));
  // counter wrap-around is handled by modular subtraction
  AquaFlowMeter w; w.begin(450.0f, 0, 0xFFFFFF00u);
  w.compute(2000, 0x000000FFu + 1u, false, 2, &o);     // 0x100 + 0x100 = 512 pulses
  CHECK(o.intervalPulses == 0x200);
  // stuck-low detection: 31 zero-pulse intervals with pin low
  AquaFlowMeter s; s.begin(450.0f, 0, 0); uint32_t t = 0; bool flagged = false;
  for (int i = 0; i < 40; i++) { t += 2000; s.compute(t, 0, true, t / 1000, &o); if (o.status & AQUA_FS_STUCK_LOW) flagged = true; }
  CHECK(flagged);
  // pin HIGH with zero flow is NOT a fault
  AquaFlowMeter z; z.begin(450.0f, 0, 0); t = 0; flagged = false;
  for (int i = 0; i < 40; i++) { t += 2000; z.compute(t, 0, false, t / 1000, &o); if (o.status & AQUA_FS_STUCK_LOW) flagged = true; }
  CHECK(!flagged);
}

// ---------------------------------------------------------------- rx tracker
static void test_rx_tracker() {
  SECTION("rx tracker");
  AquaRxTracker t; t.reset(); bool ns;
  CHECK(t.onData(10, 1, &ns) == AQUA_RX_NEW_IN_ORDER && ns);
  CHECK(t.onData(10, 2, &ns) == AQUA_RX_NEW_IN_ORDER && !ns);
  CHECK(t.onData(10, 2, &ns) == AQUA_RX_DUPLICATE);
  CHECK(t.onData(10, 5, &ns) == AQUA_RX_NEW_AHEAD);                 // 3,4 missing
  CHECK(t.cumAck == 2 && t.missingCount() == 2);
  uint32_t miss[8]; CHECK(t.missingList(miss, 8) == 2 && miss[0] == 3 && miss[1] == 4);
  CHECK(t.onData(10, 4, &ns) == AQUA_RX_RECOVERED);                 // out of order
  CHECK(t.cumAck == 2 && t.missingCount() == 1);
  CHECK(t.onData(10, 3, &ns) == AQUA_RX_RECOVERED);
  CHECK(t.cumAck == 5 && t.missingCount() == 0 && t.recovered == 2);
  CHECK(t.onData(10, 4, &ns) == AQUA_RX_DUPLICATE && t.duplicates == 2);
  // NACK scheduling
  AquaRxTracker g; g.reset(); AquaNack nk;
  g.onData(1, 1, &ns); g.onData(1, 4, &ns);                         // 2,3 missing
  CHECK(!g.poll(1000, &nk));                                        // first sight starts the timer
  CHECK(!g.poll(1100, &nk));                                        // < NACK_DELAY
  CHECK(g.poll(1400, &nk) && nk.count == 2 && nk.seqs[0] == 2 && nk.seqs[1] == 3);
  CHECK(!g.poll(1500, &nk));                                        // < REPEAT
  CHECK(g.poll(2300, &nk));
  // never answered: after NACK_ROUNDS the oldest seq is declared lost and the window moves on
  uint32_t now = 2300; int guard = 0;
  while (g.missingCount() > 0 && guard++ < 200) { now += 900; g.poll(now, &nk); }
  CHECK(g.missingCount() == 0 && g.lost == 2 && g.cumAck == 4);
  // window slide: huge jump counts everything skipped as lost
  AquaRxTracker w; w.reset(); w.onData(1, 1, &ns);
  w.onData(1, 200, &ns);
  CHECK(w.lost == 200 - 1 - 1 - 31 + 0 || w.lost > 100);            // 198 skipped, 31 still inside window
  CHECK(w.highest == 200);
  // reboot (new boot id) restarts sequence; old-boot stragglers are ignored
  AquaRxTracker r; r.reset();
  r.onData(100, 1, &ns); r.onData(100, 2, &ns);
  CHECK(r.onData(200, 1, &ns) == AQUA_RX_NEW_IN_ORDER && ns && r.sessions == 2);
  CHECK(r.onData(100, 3, &ns) == AQUA_RX_STALE_SESSION);
  CHECK(r.cumAck == 1 && r.bootId == 200);
  // gateway restart mid-session: baseline from first frame, no phantom gap chase
  AquaRxTracker q; q.reset(); q.onData(5, 1000, &ns);
  CHECK(q.cumAck == 1000 && q.missingCount() == 0);
  // heartbeat reveals trailing loss
  AquaRxTracker h; h.reset(); h.onData(1, 1, &ns); h.onHeartbeat(1, 3);
  CHECK(h.missingCount() == 2);
  // sequence near 2^32: modular arithmetic keeps working
  AquaRxTracker m; m.reset(); m.onData(1, 0xFFFFFFFEu, &ns); m.onData(1, 0xFFFFFFFFu, &ns);
  CHECK(m.onData(1, 0u, &ns) == AQUA_RX_NEW_IN_ORDER);
}

// ---------------------------------------------------------------- tx window
static void test_tx_window() {
  SECTION("tx window");
  AquaTxWindow<4, 16> w; w.reset();
  uint8_t f[16] = {0};
  for (uint32_t s = 1; s <= 4; s++) w.push(s, f, 8);
  CHECK(w.pending() == 4);
  w.push(5, f, 8);                                                  // evicts seq 1
  CHECK(w.evicted == 1 && w.dropped == 1 && w.pending() == 4);
  w.onAck(3, 0b10);                                                 // cum 3 + seq 5 (bit1 => cum+2)
  CHECK(w.pending() == 1 && w.slots[0].used + w.slots[1].used + w.slots[2].used + w.slots[3].used == 1);
  // retransmit timing + abandonment
  AquaTxWindow<2, 16> r; r.reset(); r.push(10, f, 8);
  uint32_t t = 100; int sends = 0;
  for (; t < 40000; t += 50) {
    int i = r.next(t);
    if (i >= 0) { r.markSent(i, t); sends++; }
  }
  CHECK(sends == AQUA_MAX_ATTEMPTS && r.dropped == 1 && r.pending() == 0);
  // NACK forces an immediate resend
  AquaTxWindow<2, 16> n; n.reset(); n.push(1, f, 8); int i0 = n.next(10); n.markSent(i0, 10);
  CHECK(n.next(20) < 0);
  n.requestResend(1); CHECK(n.next(20) >= 0);
}

static void test_discovery_and_channels() {
  SECTION("discovery + channel changes (hotspot hop)");
  Sim s; s.begin(); s.net.gwChannel = 6; s.gw.channel = 6;
  s.run(15000);
  CHECK(s.nodes[0].link.state() == SimLink::ONLINE && s.nodes[1].link.state() == SimLink::ONLINE);
  CHECK(s.nodes[0].link.channel() == 6 && s.nodes[1].link.channel() == 6);
  CHECK(s.distinctSeq(1, s.nodes[0].link.bootId()) >= 5);
  // hotspot moves to channel 11: gateway follows its STA channel, nodes must rediscover on their own
  s.gw.channel = 11;
  s.run(40000);
  CHECK(s.nodes[0].link.state() == SimLink::ONLINE && s.nodes[0].link.channel() == 11);
  CHECK(s.nodes[1].link.state() == SimLink::ONLINE && s.nodes[1].link.channel() == 11);
  CHECK(s.nodes[0].link.stats().rediscoveries >= 1);
  // a channel at the very top of the range
  s.gw.channel = 13; s.run(40000);
  CHECK(s.nodes[0].link.channel() == 13 && s.nodes[1].link.channel() == 13);
  // channel 1 (first of range)
  s.gw.channel = 1; s.run(40000);
  CHECK(s.nodes[0].link.channel() == 1);
}

static void test_gateway_down_then_up() {
  SECTION("gateway outage / hotspot unavailable at startup");
  Sim s; s.begin(); s.net.gwUp = false; s.gw.channel = 9;
  s.run(60000);                                        // gateway not available for a minute
  CHECK(s.nodes[0].link.state() == SimLink::SCANNING);
  CHECK(s.nodes[0].link.pending() <= 24);                   // bounded
  s.net.gwUp = true; s.run(40000);
  CHECK(s.nodes[0].link.state() == SimLink::ONLINE && s.nodes[0].link.channel() == 9);
  // volume accounting survives the outage via cumulative pulses
  uint32_t boot = s.nodes[0].link.bootId();
  s.run(10000);
  uint32_t rec = s.reconstructedPulses(1, boot);
  uint32_t truth = s.nodes[0].trueTotalPulses;
  CHECK(rec > 0 && truth - rec < 450 * 3);             // within a couple of intervals of ground truth
}

static void test_lossy_link_exact_volume() {
  SECTION("lossy/dup/reorder link: ACK, retransmit, NACK, no volume loss");
  Sim s; s.begin(); s.net.loss = 0.30; s.net.dup = 0.15; s.net.reorder = 0.20; s.gw.channel = 6;
  s.net.rng = 99;
  s.run(300000);                                       // 5 minutes at 30% loss each way
  CHECK(s.nodes[0].link.state() == SimLink::ONLINE);
  uint32_t boot = s.nodes[0].link.bootId();
  const auto& rx = s.gw.core.node(1).rx;
  printf("  node1: sent seq=%u distinct-at-gw=%u dup=%u recovered=%u lost=%u retrans=%u nacks=%u\n",
         s.nodes[0].link.lastSeq(), s.distinctSeq(1, boot), rx.duplicates, rx.recovered, rx.lost,
         (unsigned)s.nodes[0].link.stats().retransmits, rx.nacksSent);
  CHECK(rx.duplicates > 0);                            // duplicates were seen and suppressed
  CHECK(rx.recovered > 0);                             // gaps were refilled (retransmit/out-of-order)
  // every distinct sequence is published exactly once (duplicates never forwarded)
  std::vector<uint32_t> seqs;
  for (auto& t : s.gw.published) if (t.nodeId == 1 && t.bootId == boot) seqs.push_back(t.seq);
  std::sort(seqs.begin(), seqs.end());
  CHECK(std::adjacent_find(seqs.begin(), seqs.end()) == seqs.end());
  // delivery ratio
  double ratio = (double)s.distinctSeq(1, boot) / s.nodes[0].link.lastSeq();
  printf("  delivery ratio at 30%% loss: %.3f\n", ratio);
  CHECK(ratio > 0.97);
  // cumulative pulse counter reconciliation: last published cum vs ground truth
  uint32_t rec = s.reconstructedPulses(1, boot);
  CHECK(s.nodes[0].trueTotalPulses - rec < 450 * 12);  // only the not-yet-delivered tail is missing
}

static void test_node_and_gateway_reboot() {
  SECTION("node reboot + gateway reboot");
  Sim s; s.begin(); s.gw.channel = 6; s.run(20000);
  uint32_t boot1 = s.nodes[0].link.bootId();
  uint32_t cum1 = s.reconstructedPulses(1, boot1);
  CHECK(cum1 > 0);
  s.bootNode(0, 777);                                   // node 1 reboots: new boot id, counter restarts at 0
  s.run(20000);
  uint32_t boot2 = s.nodes[0].link.bootId();
  CHECK(boot2 != boot1);
  CHECK(s.nodes[0].link.state() == SimLink::ONLINE);
  uint32_t cum2 = s.reconstructedPulses(1, boot2);
  CHECK(cum2 > 0 && cum2 < cum1 + 450 * 10);            // new epoch counted separately from the old one
  CHECK(s.gw.core.node(1).rx.sessions == 2);
  // gateway reboots: state lost, nodes stay ONLINE (they still get ACKs after re-binding via heartbeat?)
  s.gw.core.begin(KEY, GWMAC, 0x78);                    // RAM state lost; the registry is restored from NVS
  s.registerNode(0, true); s.registerNode(1, true);
  s.run(60000);
  // gateway lost the session keys => ignores data until the node re-handshakes; node detects missing ACKs and rescans
  CHECK(s.nodes[0].link.state() == SimLink::ONLINE && s.nodes[1].link.state() == SimLink::ONLINE);
  CHECK(s.nodes[0].link.stats().rediscoveries >= 1);
  CHECK(s.distinctSeq(1, s.nodes[0].link.bootId()) > 0);
}

static void test_security_rejections() {
  SECTION("gateway rejects forged / unknown frames");
  AquaGatewayCore core; core.begin(KEY, GWMAC, 1);
  uint8_t mac[6], kp[16], salt[8]; testMac(0, mac); testKp(0, kp); testSalt(0, salt);
  CHECK(core.registerNode(1, 1, AQUA_DEV_FLOW, mac, kp, salt, false));
  static const uint8_t evil[6] = {0x5C,0xCF,0x7F,6,6,6};
  AquaGwOut out; AquaTelemetry tel; bool tv; uint8_t buf[AQUA_MAX_FRAME];
  AquaFlowPayload f; memset(&f, 0, sizeof(f)); f.intervalMs = 2000; f.pulsesPerLiterX10 = 4500; f.batteryMv = 7400;
  // data before the handshake: no session key yet -> refused, nothing forwarded
  size_t n = aquaBuildFrame(buf, AQUA_PKT_DATA, 1, 5, 1, 0, &f, sizeof(f), KEY);
  CHECK(!core.onFrame(mac, buf, n, 100, 0, 6, -50, -40, 3, &out, &tel, &tv) && !tv);
  // discovery is answered (PSK-tagged) and carries the real channel + gateway MAC
  AquaDiscoveryRequest rq; rq.nonce = 42; rq.probeChannel = 6; rq.fwVersion = 1;
  n = aquaBuildFrame(buf, AQUA_PKT_DISCOVERY_REQUEST, 0, 5, 1, 0, &rq, sizeof(rq), KEY);
  CHECK(core.onFrame(mac, buf, n, 100, 0, 6, -50, -40, 3, &out, &tel, &tv) && out.broadcast);
  AquaHeader h; const uint8_t* p;
  CHECK(aquaParseFrame(out.frame, out.len, KEY, &h, &p) == AQUA_OK && h.type == AQUA_PKT_DISCOVERY_RESPONSE);
  AquaDiscoveryResponse rs; memcpy(&rs, p, sizeof(rs));
  CHECK(rs.nonce == 42 && rs.channel == 6 && memcmp(rs.gwMac, GWMAC, 6) == 0);
  // after the real handshake data is accepted from the registered MAC only
  ManualNode m; memcpy(m.mac, mac, 6); memcpy(m.kp, kp, 16); uint32_t now = 1000;
  CHECK(m.join(core, now));
  n = m.data(buf, f);
  CHECK(core.onFrame(mac, buf, n, now + 100, 0, 6, -50, -40, 3, &out, &tel, &tv) && tv && tel.seq == 1 && tel.number == 1);
  CHECK(!core.onFrame(evil, buf, n, now + 110, 0, 6, -50, -40, 3, &out, &tel, &tv));          // impostor MAC, valid key
  CHECK(core.totals().unknownMac >= 1);
  // forged tag (attacker holding only the site PSK, not the node key)
  n = m.data(buf, f, KEY);
  CHECK(!core.onFrame(mac, buf, n, now + 120, 0, 6, -50, -40, 3, &out, &tel, &tv) && core.totals().badTag >= 1);
  // physically impossible measurement: acked (so the node stops resending) but never forwarded
  AquaFlowPayload bad = f; bad.flowMlPerMin = 900000;
  n = m.data(buf, bad);
  CHECK(core.onFrame(mac, buf, n, now + 130, 0, 6, -50, -40, 3, &out, &tel, &tv) && !tv && core.node(1).invalid == 1);
  // a flow node cannot push a rain-shaped payload
  AquaRainPayload rp; memset(&rp, 0, sizeof(rp)); rp.levelX10 = 500;
  n = aquaBuildFrame(buf, AQUA_PKT_DATA, 1, m.boot, 99, AQUA_HF_RAIN, &rp, sizeof(rp), m.key);
  CHECK(core.onFrame(mac, buf, n, now + 140, 0, 6, -50, -40, 3, &out, &tel, &tv) && !tv && core.node(1).invalid == 2);
  // an UNKNOWN MAC never gets a challenge: it is only listed as "nearby"
  size_t na = ManualNode{}.announce(buf);
  CHECK(!core.onFrame(evil, buf, na, now + 150, 0, 6, -50, -40, 3, &out, &tel, &tv));
  CHECK(core.nearbyCount(now + 150) == 1);
}

// ---------------------------------------------------------------- valve
static AquaValveInputs inp(bool rain, bool tv, float lvl, bool uv, uint16_t adc) { AquaValveInputs i; i.rainRaw = rain; i.tankValid = tv; i.levelPct = lvl; i.turbValid = uv; i.turbAdc = adc; return i; }

static void test_valve() {
  SECTION("valve interlock state machine");
  AquaValveConfig c; AquaValveController v; v.begin(c, 0);
  uint32_t t = 0; AquaValveOutput o;
  auto run = [&](uint32_t ms, AquaValveInputs in) { for (uint32_t e = 0; e < ms; e += 100) { t += 100; o = v.step(t, in); } };
  run(60000, inp(false, true, 40, true, 3500)); CHECK(!o.valveOpen && o.state == AQUA_VS_CLOSED_IDLE);
  run(5000, inp(true, true, 40, true, 3500));   CHECK(!o.valveOpen);                  // rain not yet confirmed
  run(10000, inp(true, true, 40, true, 3500));  CHECK(o.rainActive && !o.valveOpen && o.state == AQUA_VS_FIRST_FLUSH);
  run(60000, inp(true, true, 40, true, 3500));  CHECK(!o.valveOpen);                  // still first-flush diverting
  run(70000, inp(true, true, 40, true, 3500));  CHECK(o.valveOpen && o.state == AQUA_VS_OPEN_COLLECTING);
  // turbid water closes it (after confirm time) and raises the alarm once
  bool alarm = false; for (int i = 0; i < 100; i++) { t += 100; o = v.step(t, inp(true, true, 40, true, 1500)); if (o.alarm == AQUA_ALARM_UNSAFE_WATER) alarm = true; }
  CHECK(alarm && !o.valveOpen && o.state == AQUA_VS_CLOSED_UNSAFE);
  // water must be clear for a stable period AND min-switch interval before reopening
  run(5000, inp(true, true, 40, true, 3500));   CHECK(!o.valveOpen);
  run(30000, inp(true, true, 40, true, 3500));  CHECK(o.valveOpen);
  // tank full closes immediately, hysteresis prevents re-open until <= 90 %
  alarm = false; for (int i = 0; i < 5; i++) { t += 100; o = v.step(t, inp(true, true, 96, true, 3500)); if (o.alarm == AQUA_ALARM_TANK_FULL) alarm = true; }
  CHECK(alarm && !o.valveOpen && o.tankFull);
  run(60000, inp(true, true, 93, true, 3500));  CHECK(!o.valveOpen && o.tankFull);   // between 90 and 95: still full
  run(60000, inp(true, true, 85, true, 3500));  CHECK(o.valveOpen && !o.tankFull);
  // ultrasonic timeout => INVALID (never 0 %): fault after timeout, valve closes, no open on rain alone
  run(20000, inp(true, false, 0, true, 3500));  CHECK(!o.valveOpen && (o.faults & AQUA_RFAULT_ULTRASONIC) && o.state == AQUA_VS_FAULT_SAFE);
  run(60000, inp(true, false, 0, true, 3500));  CHECK(!o.valveOpen);
  // rain stops: closes, returns to idle
  run(200000, inp(false, true, 50, true, 3500)); CHECK(!o.valveOpen && o.state == AQUA_VS_CLOSED_IDLE);
  CHECK(o.rainEvents >= 1);
  // rain sensor chatter must not toggle anything
  AquaValveController v2; v2.begin(c, 0); t = 0; int opens = 0;
  for (int i = 0; i < 6000; i++) { t += 100; o = v2.step(t, inp(i % 2 == 0, true, 40, true, 3500)); if (o.valveOpen) opens++; }
  CHECK(opens == 0);
  // fail-safe-open config is honoured only in FAULT_SAFE
  AquaValveConfig fo = c; fo.failSafeOpen = true; AquaValveController v3; v3.begin(fo, 0); t = 0;
  for (int i = 0; i < 300; i++) { t += 100; o = v3.step(t, inp(false, false, 0, true, 3500)); }
  CHECK(o.state == AQUA_VS_FAULT_SAFE && o.valveOpen);
  // turbidity sensor disconnected while raining: never opens
  AquaValveController v4; v4.begin(c, 0); t = 0; bool everOpen = false;
  for (int i = 0; i < 20000; i++) { t += 100; o = v4.step(t, inp(true, true, 40, false, 0)); everOpen |= o.valveOpen; }
  CHECK(!everOpen);
}

static void test_buzzer() {
  SECTION("buzzer scheduler");
  AquaBuzzer b; b.begin(600000);
  uint32_t t = 1000; int ons = 0; bool prev = false;
  b.alarm(AQUA_ALARM_TANK_FULL, t);
  for (int i = 0; i < 2000; i++) { t++; bool on = b.tick(t); if (on && !prev) ons++; prev = on; }
  CHECK(ons == 3 && !b.active());                        // three short beeps, then silent
  b.alarm(AQUA_ALARM_TANK_FULL, t + 10);                 // inside cool-down: suppressed
  CHECK(!b.active());
  b.alarm(AQUA_ALARM_TANK_FULL, t + 700000);             // after cool-down: allowed
  CHECK(b.active());
}

// ---------------------------------------------------------------- json
static void test_json() {
  SECTION("telemetry JSON");
  AquaTelemetry t; memset(&t, 0, sizeof(t));
  AquaFlowPayload f; memset(&f, 0, sizeof(f));
  f.intervalPulses = 18; f.cumPulses = 5555; f.flowMlPerMin = 1234; f.intervalMl = 41; f.totalMl = 12345; f.intervalMs = 2001;
  f.uptimeS = 100; f.pulsesPerLiterX10 = 4500;
  t.nodeId = 4; t.number = 2; t.devType = AQUA_DEV_FLOW; testMac(0, t.mac); f.batteryMv = 7312; t.bootId = 99; t.seq = 45; t.payloadLen = sizeof(f); memcpy(t.payload, &f, sizeof(f)); t.channel = 6; t.rssi = -55;
  char js[512]; int n = aquaJsonFlow(js, sizeof(js), t);
  CHECK(n > 0 && n < (int)sizeof(js));
  printf("  %s\n", js);
  CHECK(strstr(js, "\"node\":2") && strstr(js, "\"mac\":\"34:85:18:00:00:01\"") && strstr(js, "\"batteryMv\":7312") && !strstr(js, "\"uid\""));
  CHECK(strstr(js, "\"flowRate\":1.234") && strstr(js, "\"totalLiters\":12.345") && strstr(js, "\"pulsesPerLiter\":450.0"));
  AquaRainPayload r; memset(&r, 0, sizeof(r)); r.levelX10 = 0xFFFF; r.distanceMm = 0xFFFF; r.turbidityAdc = 2000;
  t.devType = AQUA_DEV_RAIN; t.number = 1; t.payloadLen = sizeof(r); memcpy(t.payload, &r, sizeof(r));
  char jr[768]; n = aquaJsonRain(jr, sizeof(jr), t);
  CHECK(n > 0 && n < (int)sizeof(jr) && strstr(jr, "\"waterLevel\":null") && strstr(jr, "\"levelValid\":false"));
  AquaGatewayCore core; core.begin(KEY, GWMAC, 1);
  AquaGwHealth g; memset(&g, 0, sizeof(g)); g.gatewayId = "gw-test"; g.wifiUp = true; g.channel = 6; g.qCapacity = 256;
  char jh[1800]; n = aquaJsonGatewayHeartbeat(jh, sizeof(jh), g, core, 5000);
  CHECK(n > 0 && n < (int)sizeof(jh) && jh[n - 1] == '}' && strstr(jh, "\"nodes\":["));
  printf("  heartbeat json bytes: %d\n", n);
}

static void test_battery() {
  printf("[battery: real readings, floating pins, implausible values]\n");
  uint16_t b[2] = {0, 0};
  auto bat = [](std::vector<uint16_t> v) { return aquaBatteryMv(v.data(), (int)v.size(), 220000, 100000); };
  // real 2S pack through 220k/100k: 7.76 V -> 2425 mV at the pin, small ADC noise
  CHECK(bat({2425, 2430, 2420, 2428, 2422, 2426}) >= 7700 && bat({2425, 2430, 2420, 2428, 2422, 2426}) <= 7800);
  CHECK(bat({2660, 2655, 2665, 2660}) == 8512);                                    // Node 4 on the bench: 8.5 V
  // floating pin, values seen on the bench boards
  CHECK(bat({188, 188, 188, 188}) == AQUA_BATT_NONE);                              // 0.60 V computed
  CHECK(bat({322, 322, 321, 322}) == AQUA_BATT_NONE);                              // 1.03 V computed
  CHECK(bat({2992, 2992, 2992, 2992}) == AQUA_BATT_NONE);                          // stuck near the ADC ceiling: 9.57 V is not a plausible pack
  CHECK(bat({141, 2990, 150, 2991, 283, 2800}) == AQUA_BATT_NONE);                 // follows the flow pulses: unstable
  CHECK(bat({0, 0, 0, 0}) == AQUA_BATT_NONE);                                      // pin at 0 V
  CHECK(bat({30, 40, 35, 30}) == AQUA_BATT_NONE);
  CHECK(bat({2425}) == AQUA_BATT_NONE);                                            // one sample cannot show stability
  CHECK(aquaBatteryMv(nullptr, 4, 220000, 100000) == AQUA_BATT_NONE);
  CHECK(aquaBatteryMv((const uint16_t*)&b[0], 0, 220000, 100000) == AQUA_BATT_NONE);
  // edges of the plausible window
  CHECK(bat({781, 781, 781, 781}) == AQUA_BATT_NONE);                              // 781 * 3.2 = 2499 mV: just under the window
  CHECK(bat({782, 782, 782, 782}) == 2502);                                        // just inside
  CHECK(bat({2812, 2812, 2812, 2812}) == 8998 && bat({2813, 2813, 2813, 2813}) == AQUA_BATT_NONE);   // 9000 mV upper edge
}

int main() {
  test_sizes_and_siphash();
  test_build_parse_reject();
  test_flow_meter();
  test_rx_tracker();
  test_tx_window();
  test_security_rejections();
  test_valve();
  test_buzzer();
  test_json();
  test_discovery_and_channels();
  test_gateway_down_then_up();
  test_lossy_link_exact_volume();
  test_node_and_gateway_reboot();
  test_battery();
  printf("\n%d checks passed, %d failed\n", g_pass, g_fail);
  return g_fail ? 1 : 0;
}
