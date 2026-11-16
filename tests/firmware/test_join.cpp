// Host-side tests for registry-based pairing (v2): pairing code, keys, QR payloads, handshake, per-node keys,
// nearby list, unpair / replace.  Replaces the Milestone 3 test_pairing.cpp (backend-mediated uid pairing).
#include <cstdio>
#include <cstdlib>
#include <map>
#include <set>
#include <string>
#include "AquaProtocol.h"
#include "sim_harness.h"
#include "aqua_identity_core.h"
#include "aqua_registry.h"

static int g_fail = 0, g_pass = 0;
#define CHECK(c) do { if (c) { g_pass++; } else { g_fail++; printf("  FAIL %s:%d  %s\n", __FILE__, __LINE__, #c); } } while (0)
#define SECTION(n) printf("[%s]\n", n)

static AquaFlowPayload goodFlow() {
  AquaFlowPayload f; memset(&f, 0, sizeof(f)); f.intervalMs = 2000; f.pulsesPerLiterX10 = 4500; f.batteryMv = 7400; return f;
}
static void regNode(AquaGatewayCore& core, ManualNode& m, int idx, uint8_t slot, uint8_t number, uint8_t type = AQUA_DEV_FLOW) {
  uint8_t salt[8]; testMac(idx, m.mac); testKp(idx, m.kp); testSalt(idx, salt); m.devType = type;
  CHECK(core.registerNode(slot, number, type, m.mac, m.kp, salt, false));
}

// ---------------------------------------------------------------- pairing code
static void test_code() {
  SECTION("pairing code: 12 symbols, 32-symbol alphabet, grouped, forgiving input");
  CHECK(AQUA_CODE_LEN == 12 && strlen(AQUA_CODE_ALPHABET) == 32);
  std::set<char> uniq(AQUA_CODE_ALPHABET, AQUA_CODE_ALPHABET + 32);
  CHECK(uniq.size() == 32);
  for (char bad : std::string("ILOU")) CHECK(uniq.count(bad) == 0);                 // ambiguous symbols are not in the alphabet
  uint8_t rnd[8] = {0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0}; char code[13], shown[15], norm[13];
  aquaCodeFromRandom(rnd, code);
  CHECK(strlen(code) == 12);
  for (int i = 0; i < 12; i++) CHECK(aquaCodeSymbolValue(code[i]) >= 0);
  aquaCodeFormat(code, shown);
  CHECK(strlen(shown) == 14 && shown[4] == '-' && shown[9] == '-');
  // input handling: dashes, spaces, case, reading rules O->0 I/L->1
  CHECK(aquaCodeNormalize(shown, norm) && strcmp(norm, code) == 0);
  char lower[15]; for (int i = 0; i < 14; i++) lower[i] = (shown[i] >= 'A' && shown[i] <= 'Z') ? (char)(shown[i] + 32) : shown[i]; lower[14] = 0;
  CHECK(aquaCodeNormalize(lower, norm) && strcmp(norm, code) == 0);
  CHECK(aquaCodeNormalize("abcd efgh jkmn", norm) && strcmp(norm, "ABCDEFGHJKMN") == 0);
  CHECK(aquaCodeNormalize("O1IL-0000-0000", norm) && strcmp(norm, "011100000000") == 0);
  CHECK(!aquaCodeNormalize("ABCD-EFGH-JKM", norm));                                   // 11 symbols
  CHECK(!aquaCodeNormalize("ABCD-EFGH-JKMNP", norm));                                 // 13 symbols
  CHECK(!aquaCodeNormalize("ABCD-EFGH-JKMU", norm));                                  // U is not a symbol
  CHECK(!aquaCodeNormalize("ABCD-EFGH-JKM!", norm));
  CHECK(!aquaCodeNormalize("", norm));
  // all 60 random bits are used: different random input -> different code
  uint8_t rnd2[8]; memcpy(rnd2, rnd, 8); rnd2[7] ^= 0x08; char code2[13]; aquaCodeFromRandom(rnd2, code2);
  CHECK(strcmp(code, code2) != 0);
}

// ---------------------------------------------------------------- keys
static void test_keys() {
  SECTION("pairing key / node key / session key");
  uint8_t macA[6], macB[6]; testMac(0, macA); testMac(1, macB);
  uint8_t k1[16], k2[16], k3[16], k4[16];
  aquaPairingKey("ABCDEFGHJKMN", macA, k1); aquaPairingKey("ABCDEFGHJKMN", macA, k2);
  aquaPairingKey("ABCDEFGHJKMP", macA, k3); aquaPairingKey("ABCDEFGHJKMN", macB, k4);
  CHECK(memcmp(k1, k2, 16) == 0);                                                      // deterministic
  CHECK(memcmp(k1, k3, 16) != 0 && memcmp(k1, k4, 16) != 0);                           // depends on code AND mac
  uint8_t salt1[8] = {1,2,3,4,5,6,7,8}, salt2[8] = {1,2,3,4,5,6,7,9}, n1[16], n2[16], s1[16], s2[16];
  aquaNodeKey(k1, salt1, n1); aquaNodeKey(k1, salt2, n2);
  CHECK(memcmp(n1, n2, 16) != 0 && memcmp(n1, k1, 16) != 0);
  aquaSessionKey(n1, 100, s1); aquaSessionKey(n1, 101, s2);
  CHECK(memcmp(s1, s2, 16) != 0);                                                      // new boot -> new key
  uint8_t zero[16] = {0}, rep[16]; memset(rep, 0x5A, 16);
  CHECK(aquaKeyIsWeak(zero) && aquaKeyIsWeak(rep) && !aquaKeyIsWeak(KEY));
}

// ---------------------------------------------------------------- QR
static void test_qr() {
  SECTION("QR payload parser (URL + compact)");
  AquaQr q;
  CHECK(aquaQrParse("http://192.168.4.1/add?v=1&mac=AA:BB:CC:DD:EE:FF&code=0123456789AB&type=flow", &q) == AQUA_QR_OK);
  CHECK(q.mac[0] == 0xAA && q.mac[5] == 0xFF && strcmp(q.code, "0123456789AB") == 0 && q.type == AQUA_DEV_FLOW);
  // lower case everywhere, encoded colons, dashes in the code, surrounding whitespace, another host
  CHECK(aquaQrParse("  https://aquaguard.local/add?v=1&mac=aa%3Abb%3Acc%3add%3Aee%3Aff&code=abcd-efgh-jkmn&type=RAIN \n", &q) == AQUA_QR_OK);
  CHECK(q.mac[3] == 0xDD && strcmp(q.code, "ABCDEFGHJKMN") == 0 && q.type == AQUA_DEV_RAIN);
  CHECK(aquaQrParse("http://192.168.4.1/add?type=flow&code=0123456789AB&mac=AABBCCDDEEFF&v=1", &q) == AQUA_QR_OK);   // any order, bare MAC
  // compact form
  CHECK(aquaQrParse("AQG1;AABBCCDDEEFF;0123456789AB;flow", &q) == AQUA_QR_OK && q.mac[1] == 0xBB && q.type == AQUA_DEV_FLOW);
  CHECK(aquaQrParse("aqg1;aabbccddeeff;0123-4567-89ab;Rain", &q) == AQUA_QR_OK && q.type == AQUA_DEV_RAIN);
  // errors
  CHECK(aquaQrParse("http://192.168.4.1/add?v=1&mac=AA:BB:CC:DD:EE:FF&type=flow", &q) == AQUA_QR_FORMAT);             // code missing
  CHECK(aquaQrParse("http://192.168.4.1/add?v=1&code=0123456789AB&type=flow", &q) == AQUA_QR_FORMAT);                 // mac missing
  CHECK(aquaQrParse("http://192.168.4.1/add?mac=AA:BB:CC:DD:EE:FF&code=0123456789AB&type=flow", &q) == AQUA_QR_FORMAT);   // version missing
  CHECK(aquaQrParse("http://192.168.4.1/add?v=2&mac=AA:BB:CC:DD:EE:FF&code=0123456789AB&type=flow", &q) == AQUA_QR_VERSION);
  CHECK(aquaQrParse("AQG2;AABBCCDDEEFF;0123456789AB;flow", &q) == AQUA_QR_VERSION);
  CHECK(aquaQrParse("AQG1;AABBCCDDEEFF;0123456789AB", &q) == AQUA_QR_FORMAT);                                         // too few fields
  CHECK(aquaQrParse("AQG1;AABBCCDDEEFF;0123456789AB;flow;x", &q) == AQUA_QR_FORMAT);                                  // too many
  CHECK(aquaQrParse("http://192.168.4.1/add?v=1&mac=AA:BB:CC:DD:EE&code=0123456789AB&type=flow", &q) == AQUA_QR_MAC);   // short MAC
  CHECK(aquaQrParse("http://192.168.4.1/add?v=1&mac=GG:BB:CC:DD:EE:FF&code=0123456789AB&type=flow", &q) == AQUA_QR_MAC);
  CHECK(aquaQrParse("AQG1;AABBCCDDEEFF00;0123456789AB;flow", &q) == AQUA_QR_MAC);                                     // 7 bytes
  CHECK(aquaQrParse("http://192.168.4.1/add?v=1&mac=AA:BB:CC:DD:EE:FF&code=0123&type=flow", &q) == AQUA_QR_CODE);
  CHECK(aquaQrParse("AQG1;AABBCCDDEEFF;0123456789AU;flow", &q) == AQUA_QR_CODE);
  CHECK(aquaQrParse("http://192.168.4.1/add?v=1&mac=AA:BB:CC:DD:EE:FF&code=0123456789AB&type=valve", &q) == AQUA_QR_TYPE);
  CHECK(aquaQrParse("hello world", &q) == AQUA_QR_FORMAT && aquaQrParse("", &q) == AQUA_QR_FORMAT && aquaQrParse(nullptr, &q) == AQUA_QR_FORMAT);
  // build -> parse round trip, and the documented literal
  uint8_t mac[6] = {0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF}; char out[200];
  CHECK(aquaQrBuild(mac, "0123456789AB", AQUA_DEV_FLOW, false, out, sizeof(out)) > 0);
  CHECK(strcmp(out, "http://192.168.4.1/add?v=1&mac=AA:BB:CC:DD:EE:FF&code=0123456789AB&type=flow") == 0);
  CHECK(aquaQrParse(out, &q) == AQUA_QR_OK && memcmp(q.mac, mac, 6) == 0);
  CHECK(aquaQrBuild(mac, "0123456789AB", AQUA_DEV_RAIN, true, out, sizeof(out)) > 0);
  CHECK(strcmp(out, "AQG1;AABBCCDDEEFF;0123456789AB;rain") == 0);
  CHECK(aquaQrParse(out, &q) == AQUA_QR_OK && q.type == AQUA_DEV_RAIN);
  CHECK(aquaQrBuild(mac, "0123456789AB", AQUA_DEV_FLOW, false, out, 20) == -1);                                      // buffer too small
}

// ---------------------------------------------------------------- handshake
static void test_handshake() {
  SECTION("handshake: pending -> paired, wrong code fails, mutual proof");
  AquaGatewayCore core; core.begin(KEY, GWMAC, 7);
  ManualNode m; regNode(core, m, 0, 1, 1);
  CHECK(core.node(1).state == AquaGatewayCore::NS_PENDING && !core.node(1).keyed);
  uint32_t now = 1000; AquaPairEvent ev;
  CHECK(m.join(core, now) && m.slot == 1);
  CHECK(core.node(1).state == AquaGatewayCore::NS_PAIRED && core.node(1).keyed);
  CHECK(core.popEvent(&ev) && ev.kind == AQUA_PE_PAIR_OK && ev.firstTime && ev.slot == 1);
  // a second join (node reboot) is OK but no longer "first time"
  ManualNode m2 = m; m2.boot = 0xBEEF0002u;
  CHECK(m2.join(core, now));
  CHECK(core.popEvent(&ev) && ev.kind == AQUA_PE_PAIR_OK && !ev.firstTime);
  // wrong pairing key: gateway refuses, reports a failure, installs no key
  AquaGatewayCore c2; c2.begin(KEY, GWMAC, 8); ManualNode bad; regNode(c2, bad, 1, 2, 1); now = 1000;
  CHECK(!bad.join(c2, now, true));
  CHECK(!c2.node(2).keyed && c2.node(2).state == AquaGatewayCore::NS_PENDING && c2.totals().pairFail == 1);
  CHECK(c2.popEvent(&ev) && ev.kind == AQUA_PE_PAIR_FAIL);
  uint8_t buf[AQUA_MAX_FRAME]; AquaGwOut out; AquaTelemetry tel; bool tv;
  AquaFlowPayload gf = goodFlow();
  size_t n = aquaBuildFrame(buf, AQUA_PKT_DATA, 2, bad.boot, 1, 0, &gf, sizeof(gf), KEY);
  CHECK(!c2.onFrame(bad.mac, buf, n, now + 50, 0, 6, -50, -40, 3, &out, &tel, &tv) && !tv);     // still no data accepted
}

static void test_fake_gateway() {
  SECTION("a node does not accept a gateway that does not know its pairing key");
  // Sim node + a hostile 'gateway' that holds the site PSK but not the node's pairing key
  Sim s; s.net.now = 1000; s.gw.core.begin(KEY, GWMAC, 5);
  uint8_t mac[6], wrongKp[16] = {9}, salt[8] = {1}; testMac(0, mac);
  s.gw.core.registerNode(1, 1, AQUA_DEV_FLOW, mac, wrongKp, salt, false);                      // attacker's idea of the node's key
  s.bootNode(0, 111); s.nodes[1].powered = false; s.nodes[2].powered = false;
  s.run(15000);
  CHECK(s.nodes[0].link.state() == SimLink::JOINING && !s.nodes[0].link.joined());               // never goes ONLINE
  CHECK(s.nodes[0].link.stats().pairings == 0 && s.gw.published.empty() && s.gw.core.totals().pairFail >= 1);   // gateway side: wrong key
  // node side: a forged ACCEPT whose proof was computed WITHOUT the node's pairing key is refused
  const uint8_t* chal = s.gw.core.node(1).chal; AquaPairAccept a; memcpy(a.mac, mac, 6); a.slot = 1; memcpy(a.salt, salt, 8);
  aquaJoinMac(wrongKp, 'A', mac, s.nodes[0].link.bootId(), chal, 1, salt, a.proof);
  uint8_t fb[AQUA_MAX_FRAME]; size_t fn = aquaBuildFrame(fb, AQUA_PKT_PAIR_ACCEPT, 0, s.nodes[0].link.bootId(), 99, 0, &a, sizeof(a), KEY);
  s.nodes[0].link.onReceive(GWMAC, fb, fn);
  CHECK(!s.nodes[0].link.joined() && s.nodes[0].link.stats().pairFailures == 1);
}

// ---------------------------------------------------------------- per-node keys
static void test_per_node_keys() {
  SECTION("per-node data keys: one node's key cannot speak for another");
  AquaGatewayCore core; core.begin(KEY, GWMAC, 9);
  ManualNode a, b; regNode(core, a, 0, 1, 1); regNode(core, b, 1, 2, 2);
  uint32_t now = 1000; CHECK(a.join(core, now)); CHECK(b.join(core, now));
  CHECK(memcmp(a.key, b.key, 16) != 0);
  AquaGwOut out; AquaTelemetry tel; bool tv; uint8_t buf[AQUA_MAX_FRAME];
  AquaFlowPayload f = goodFlow();
  size_t n = a.data(buf, f);
  CHECK(core.onFrame(a.mac, buf, n, now + 10, 0, 6, -50, -40, 3, &out, &tel, &tv) && tv && tel.number == 1);
  // node B (holding only its own key) forges a frame for slot 1, from its own MAC and from A's MAC
  n = aquaBuildFrame(buf, AQUA_PKT_DATA, 1, a.boot, 50, 0, &f, sizeof(f), b.key);
  CHECK(!core.onFrame(b.mac, buf, n, now + 20, 0, 6, -50, -40, 3, &out, &tel, &tv));
  CHECK(!core.onFrame(a.mac, buf, n, now + 30, 0, 6, -50, -40, 3, &out, &tel, &tv) && core.totals().badTag >= 1);
  // using the shared site PSK is not enough either
  n = aquaBuildFrame(buf, AQUA_PKT_DATA, 1, a.boot, 51, 0, &f, sizeof(f), KEY);
  CHECK(!core.onFrame(a.mac, buf, n, now + 40, 0, 6, -50, -40, 3, &out, &tel, &tv));
  // a captured old-boot key does not work in the next boot (session key = f(boot))
  ManualNode a2 = a; a2.boot = a.boot + 1; CHECK(a2.join(core, now));
  CHECK(memcmp(a2.key, a.key, 16) != 0);
  n = aquaBuildFrame(buf, AQUA_PKT_DATA, 1, a2.boot, 1, 0, &f, sizeof(f), a.key);
  CHECK(!core.onFrame(a.mac, buf, n, now + 50, 0, 6, -50, -40, 3, &out, &tel, &tv));
}

static void test_challenge_rules() {
  SECTION("challenge: single use, rate limited, type and MAC bound");
  AquaGatewayCore core; core.begin(KEY, GWMAC, 11);
  ManualNode m; regNode(core, m, 0, 1, 1);
  uint8_t buf[AQUA_MAX_FRAME]; AquaGwOut out; AquaTelemetry tel; bool tv; AquaHeader h; const uint8_t* p;
  size_t n = m.announce(buf);
  CHECK(core.onFrame(m.mac, buf, n, 5000, 0, 6, -50, -40, 3, &out, &tel, &tv));
  CHECK(!core.onFrame(m.mac, buf, n, 5400, 0, 6, -50, -40, 3, &out, &tel, &tv));               // < 1 s later: no new challenge
  CHECK(core.onFrame(m.mac, buf, n, 6100, 0, 6, -50, -40, 3, &out, &tel, &tv));
  AquaPairChallenge c; aquaParseFrame(out.frame, out.len, KEY, &h, &p); memcpy(&c, p, sizeof(c));
  // a response computed for the FIRST challenge (replay) does not work against the second one
  uint8_t zero8[8] = {0}, oldChal[8] = {1,2,3,4,5,6,7,8}; AquaPairResponse r;
  aquaJoinMac(m.kp, 'R', m.mac, m.boot, oldChal, 0, zero8, r.resp);
  n = aquaBuildFrame(buf, AQUA_PKT_PAIR_RESPONSE, 0, m.boot, 2, 0, &r, sizeof(r), KEY);
  CHECK(!core.onFrame(m.mac, buf, n, 6200, 0, 6, -50, -40, 3, &out, &tel, &tv) && core.totals().pairFail == 1);
  // the response is single use: a correct one is rejected once the challenge was consumed
  aquaJoinMac(m.kp, 'R', m.mac, m.boot, c.chal, 0, zero8, r.resp);
  n = aquaBuildFrame(buf, AQUA_PKT_PAIR_RESPONSE, 0, m.boot, 3, 0, &r, sizeof(r), KEY);
  CHECK(!core.onFrame(m.mac, buf, n, 6300, 0, 6, -50, -40, 3, &out, &tel, &tv) && !core.node(1).keyed);
  // a rain announce for a node registered as flow is refused
  ManualNode wrong = m; wrong.devType = AQUA_DEV_RAIN; n = wrong.announce(buf);
  CHECK(!core.onFrame(m.mac, buf, n, 9000, 0, 6, -50, -40, 3, &out, &tel, &tv));
  // handshake frames with a bad PSK tag are dropped before anything else
  n = aquaBuildFrame(buf, AQUA_PKT_PAIR_ANNOUNCE, 0, m.boot, 1, 0, &c, 4, m.kp);
  CHECK(!core.onFrame(m.mac, buf, n, 12000, 0, 6, -50, -40, 3, &out, &tel, &tv));
}

// ---------------------------------------------------------------- nearby
static void test_nearby() {
  SECTION("unknown nodes: listed as nearby, never paired automatically");
  AquaGatewayCore core; core.begin(KEY, GWMAC, 3);
  uint8_t buf[AQUA_MAX_FRAME]; AquaGwOut out; AquaTelemetry tel; bool tv; AquaPairEvent ev;
  ManualNode u; testMac(2, u.mac); testKp(2, u.kp);
  size_t n = u.announce(buf);
  CHECK(!core.onFrame(u.mac, buf, n, 1000, 0, 6, -61, -40, 3, &out, &tel, &tv));                // no reply: not registered
  CHECK(core.nearbyCount(1000) == 1);
  AquaNearby nb; CHECK(core.nearbyAt(0, 1000, &nb) && memcmp(nb.mac, u.mac, 6) == 0 && nb.rssi == -61 && nb.devType == AQUA_DEV_FLOW);
  CHECK(core.popEvent(&ev) && ev.kind == AQUA_PE_DISCOVERED && memcmp(ev.mac, u.mac, 6) == 0);
  core.onFrame(u.mac, buf, n, 3000, 0, 6, -60, -40, 3, &out, &tel, &tv);                        // repeats inside 10 s: no new event
  CHECK(!core.popEvent(&ev) && core.nearbyCount(3000) == 1 && core.totals().nearbyEvents == 1);
  CHECK(core.nearbyAt(0, 3000, &nb) && nb.announces == 2 && nb.rssi == -60);
  core.onFrame(u.mac, buf, n, 12000, 0, 6, -60, -40, 3, &out, &tel, &tv);                       // >10 s later: one more event
  CHECK(core.popEvent(&ev));
  CHECK(core.nearbyCount(12000 + AquaGatewayCore::NEARBY_TTL_MS + 1) == 0);                      // stops announcing -> disappears
  // an announcing flood of different MACs cannot grow the table beyond its size
  for (int i = 0; i < 40; i++) { uint8_t m2[6] = {0x02, 0, 0, 0, 0, (uint8_t)i}; core.onFrame(m2, buf, n, 20000 + i, 0, 6, -70, -40, 3, &out, &tel, &tv); }
  CHECK(core.nearbyCount(20100) == AquaGatewayCore::NEARBY_N);
  // registering a nearby node (the owner typed its code) removes it from the list and lets it handshake
  AquaGatewayCore c2; c2.begin(KEY, GWMAC, 4);
  c2.onFrame(u.mac, buf, n, 1000, 0, 6, -61, -40, 3, &out, &tel, &tv);
  CHECK(c2.nearbyCount(1000) == 1);
  uint8_t salt[8]; testSalt(2, salt);
  CHECK(c2.registerNode(3, 1, AQUA_DEV_FLOW, u.mac, u.kp, salt, false) && c2.nearbyCount(1000) == 0);
  uint32_t now = 2000; CHECK(u.join(c2, now) && u.slot == 3);
}

// ---------------------------------------------------------------- registry
static void test_registry() {
  SECTION("registry: duplicates refused, remove -> UNPAIR, replace keeps slot and number");
  AquaGatewayCore core; core.begin(KEY, GWMAC, 12);
  ManualNode a, b; regNode(core, a, 0, 1, 1);
  uint8_t mac[6], kp[16], salt[8]; testMac(0, mac); testKp(0, kp); testSalt(0, salt);
  CHECK(!core.registerNode(2, 2, AQUA_DEV_FLOW, mac, kp, salt, false));                         // duplicate MAC
  testMac(1, mac);
  CHECK(!core.registerNode(1, 2, AQUA_DEV_FLOW, mac, kp, salt, false));                         // duplicate slot
  CHECK(!core.registerNode(0, 2, AQUA_DEV_FLOW, mac, kp, salt, false));                         // slot 0 is not a slot
  CHECK(!core.registerNode(AQUA_MAX_SLOTS + 1, 2, AQUA_DEV_FLOW, mac, kp, salt, false));
  CHECK(!core.registerNode(2, 2, 9, mac, kp, salt, false));                                     // unknown type
  CHECK(core.registerNode(2, 1, AQUA_DEV_RAIN, mac, kp, salt, false));                          // the rain node lives in the same registry ("Rain 1")
  CHECK(core.registeredCount() == 2);
  uint32_t now = 1000; CHECK(a.join(core, now));
  AquaGwOut out; AquaTelemetry tel; bool tv; uint8_t buf[AQUA_MAX_FRAME]; AquaHeader h; const uint8_t* p;
  // remove: an UNPAIR frame tagged with the node's session key is produced; the slot is freed
  CHECK(core.removeNode(1, &out) && !out.broadcast && memcmp(out.toMac, a.mac, 6) == 0);
  CHECK(aquaParseFrame(out.frame, out.len, a.key, &h, &p) == AQUA_OK && h.type == AQUA_PKT_UNPAIR && h.nodeId == 1 && h.bootId == a.boot);
  CHECK(aquaParseFrame(out.frame, out.len, KEY, &h, &p) == AQUA_E_TAG);                           // not forgeable with the PSK
  CHECK(core.node(1).state == AquaGatewayCore::NS_FREE && core.registeredCount() == 1);
  size_t n = a.data(buf, goodFlow());
  CHECK(!core.onFrame(a.mac, buf, n, now + 100, 0, 6, -50, -40, 3, &out, &tel, &tv) && !tv);      // data after removal is refused
  n = a.announce(buf); core.onFrame(a.mac, buf, n, now + 3000, 0, 6, -50, -40, 3, &out, &tel, &tv);
  CHECK(core.nearbyCount(now + 3000) == 1);                                                       // it shows up as nearby again, never auto-paired
  CHECK(!core.removeNode(1, &out));                                                               // second remove: nothing to unpair
  // replace: same slot + number, new hardware
  ManualNode oldN, newN; regNode(core, oldN, 2, 3, 2);                                            // "Node 2" in slot 3
  now = 20000; CHECK(oldN.join(core, now));
  testMac(0, newN.mac); testKp(0, newN.kp); uint8_t s2[8]; testSalt(5, s2);
  CHECK(core.replaceNode(3, newN.mac, newN.kp, s2) && core.node(3).number == 2 && core.node(3).state == AquaGatewayCore::NS_PENDING);
  CHECK(!core.node(3).keyed && core.findByMac(oldN.mac) < 0);
  n = oldN.data(buf, goodFlow());
  CHECK(!core.onFrame(oldN.mac, buf, n, now + 100, 0, 6, -50, -40, 3, &out, &tel, &tv));          // the old unit is locked out
  newN.boot = 0xCAFE0001u; CHECK(newN.join(core, now) && newN.slot == 3);
  n = newN.data(buf, goodFlow());
  CHECK(core.onFrame(newN.mac, buf, n, now + 500, 0, 6, -50, -40, 3, &out, &tel, &tv) && tv && tel.number == 2 && tel.nodeId == 3);
  CHECK(!core.replaceNode(9, newN.mac, newN.kp, s2) && !core.replaceNode(1, mac, kp, s2) /* free slot */);
}

// ---------------------------------------------------------------- simulated radio
static void test_sim_join_and_unpair() {
  SECTION("simulated radio: join, battery in telemetry, volume starts at the join, UNPAIR");
  Sim s; s.begin(); s.gw.channel = 6; s.run(12000);
  CHECK(s.nodes[0].link.state() == SimLink::ONLINE && s.nodes[1].link.state() == SimLink::ONLINE);
  CHECK(s.nodes[0].link.slot() == 1 && s.nodes[1].link.slot() == 2 && s.nodes[0].link.stats().pairings == 1);
  CHECK(s.gw.core.node(1).state == AquaGatewayCore::NS_PAIRED);
  bool sawBatt = false; uint32_t firstCum = 0xFFFFFFFFu;
  for (auto& t : s.gw.published) if (t.number == 1) {
    AquaFlowPayload f; memcpy(&f, t.payload, sizeof(f)); if (f.batteryMv == 7400) sawBatt = true;
    if (firstCum == 0xFFFFFFFFu) firstCum = f.cumPulses;
  }
  CHECK(sawBatt);
  CHECK(firstCum < 450);                                                                          // pre-join pulses (scan time) are not in the record
  uint32_t boot = s.nodes[0].link.bootId();
  CHECK(s.reconstructedPulses(1, boot) <= s.nodes[0].trueTotalPulses && s.nodes[0].trueTotalPulses - s.reconstructedPulses(1, boot) < 450 * 8);
  // registry configuration (calibration + TX flag) travels in the ACKs and reaches the node exactly once per change
  uint16_t ppl = 0; uint8_t cf = 0;
  CHECK(!s.nodes[0].link.takeConfig(&ppl, &cf));
  CHECK(s.gw.core.setNodeConfig(1, 4620, AQUA_CFG_TX_REDUCE)); s.run(3000);
  CHECK(s.nodes[0].link.takeConfig(&ppl, &cf) && ppl == 4620 && cf == AQUA_CFG_TX_REDUCE);
  CHECK(!s.nodes[0].link.takeConfig(&ppl, &cf));
  CHECK(!s.nodes[1].link.takeConfig(&ppl, &cf));                                                   // other node untouched
  CHECK(!s.gw.core.setNodeConfig(7, 4500, 0));                                                     // unregistered slot
  // the owner removes node 1: UNPAIR reaches the node, which restarts unpaired; node 2 is untouched
  AquaGwOut o; CHECK(s.gw.core.removeNode(1, &o)); s.gwSend(o);
  s.run(300);
  CHECK(s.nodes[0].link.rebootRequested() && s.nodes[0].link.unpairedByGateway());
  CHECK(!s.nodes[1].link.rebootRequested() && s.nodes[1].link.state() == SimLink::ONLINE);
  // after the restart the node announces; the gateway only lists it as nearby until the owner re-adds it
  s.bootNode(0, 555); s.run(15000);
  CHECK(s.nodes[0].link.state() == SimLink::JOINING && !s.nodes[0].link.joined());
  CHECK(s.gw.core.nearbyCount(s.net.now) == 1);
  CHECK(s.registerNode(0, false)); s.run(15000);                                                  // owner types the code -> it joins
  CHECK(s.nodes[0].link.state() == SimLink::ONLINE && s.nodes[0].link.joined());
}

static void test_sim_wrong_code_then_right_code() {
  SECTION("simulated radio: node with a different code never joins");
  Sim s; s.net.now = 1000; s.gw.core.begin(KEY, GWMAC, 0x77); s.registerNode(0);
  s.bootNode(0, 111, /*codeIndex=*/1);                                                            // node holds code #1, gateway expects code #0
  s.nodes[1].powered = false; s.nodes[2].powered = false;
  s.run(20000);
  CHECK(!s.nodes[0].link.joined() && s.gw.published.empty() && s.gw.core.totals().pairFail >= 1);
  CHECK(s.gw.core.node(1).state == AquaGatewayCore::NS_PENDING);
}

// ---- identity: factory reset KEEPS the code, regen-code replaces it -------------------------------------
struct FakeStore {
  std::map<std::string, std::string> m;
  bool getStr(const char* k, char* out, size_t cap) { auto i = m.find(k); if (i == m.end() || i->second.size() + 1 > cap) return false; strcpy(out, i->second.c_str()); return true; }
  void putStr(const char* k, const char* v) { m[k] = v; }
  bool getBool(const char* k, bool d) { auto i = m.find(k); return i == m.end() ? d : i->second == "1"; }
  void putBool(const char* k, bool v) { m[k] = v ? "1" : "0"; }
  uint16_t getU16(const char* k, uint16_t d) { auto i = m.find(k); return i == m.end() ? d : (uint16_t)atoi(i->second.c_str()); }
  void putU16(const char* k, uint16_t v) { m[k] = std::to_string(v); }
  uint8_t getU8(const char* k, uint8_t d) { auto i = m.find(k); return i == m.end() ? d : (uint8_t)atoi(i->second.c_str()); }
  void putU8(const char* k, uint8_t v) { m[k] = std::to_string(v); }
  void remove(const char* k) { m.erase(k); }
};

static void test_identity() {
  SECTION("identity: factory reset keeps the pairing code; regen-code replaces it");
  const uint8_t mac[6] = {0x34, 0x85, 0x18, 0xAA, 0xBB, 0x01};
  const uint8_t rndA[8] = {1, 2, 3, 4, 5, 6, 7, 8}, rndB[8] = {9, 8, 7, 6, 5, 4, 3, 2}, rndC[8] = {0xAA, 0x55, 0xAA, 0x55, 1, 2, 3, 4};
  FakeStore st; AquaIdentityCore<FakeStore> id;
  CHECK(id.begin(&st, AQUA_DEV_FLOW, mac, rndA));
  char code0[13]; strcpy(code0, id.code()); uint8_t kp0[16]; memcpy(kp0, id.pairingKey(), 16);
  CHECK(strlen(code0) == 12);
  id.setPaired(true); id.setPplX10(4321); id.setCfgFlags(AQUA_CFG_TX_REDUCE);
  { AquaIdentityCore<FakeStore> id2; CHECK(!id2.begin(&st, AQUA_DEV_FLOW, mac, rndB)); CHECK(strcmp(id2.code(), code0) == 0); }

  id.factoryReset();
  CHECK(!id.wasPaired() && id.pplX10(4500) == 4500 && id.cfgFlags() == 0);
  CHECK(strcmp(id.code(), code0) == 0 && memcmp(id.pairingKey(), kp0, 16) == 0);
  { AquaIdentityCore<FakeStore> id3; CHECK(!id3.begin(&st, AQUA_DEV_FLOW, mac, rndB)); CHECK(strcmp(id3.code(), code0) == 0); }
  CHECK(st.m["code"] == code0);

  id.setPaired(true);
  id.regenCode(rndC);
  CHECK(strcmp(id.code(), code0) != 0 && strlen(id.code()) == 12);
  CHECK(memcmp(id.pairingKey(), kp0, 16) != 0 && !id.wasPaired());
  { AquaIdentityCore<FakeStore> id4; CHECK(!id4.begin(&st, AQUA_DEV_FLOW, mac, rndA)); CHECK(strcmp(id4.code(), id.code()) == 0); }
  st.m["code"] = "garbage"; { AquaIdentityCore<FakeStore> id5; CHECK(id5.begin(&st, AQUA_DEV_FLOW, mac, rndB)); CHECK(strlen(id5.code()) == 12); }
}

static void test_regen_confirm() {
  SECTION("serial command: regen-code needs an explicit, fresh confirmation");
  AquaRegenConfirm c;
  CHECK(c.onLine("regen-code confirm", 1000) == AquaRegenConfirm::CANCELLED);
  CHECK(c.onLine("hello", 1000) == AquaRegenConfirm::NONE);
  CHECK(c.onLine("regen-code", 2000) == AquaRegenConfirm::ASK_CONFIRM);
  CHECK(c.onLine("regen-code confirm", 3000) == AquaRegenConfirm::DO_REGEN);
  CHECK(c.onLine("regen-code confirm", 3500) == AquaRegenConfirm::CANCELLED);
  CHECK(c.onLine("  regen-code \r", 10000) == AquaRegenConfirm::ASK_CONFIRM);
  CHECK(c.onLine("regen-code confirm", 10000 + AquaRegenConfirm::WINDOW_MS + 1) == AquaRegenConfirm::CANCELLED);
  CHECK(c.onLine("regen-code", 50000) == AquaRegenConfirm::ASK_CONFIRM);
  CHECK(c.onLine("something else", 50100) == AquaRegenConfirm::CANCELLED);
  CHECK(c.onLine("regen-code confirm", 50200) == AquaRegenConfirm::CANCELLED);
  CHECK(c.onLine("regen-codes", 60000) == AquaRegenConfirm::NONE);
}

static void test_registry_sync() {
  SECTION("registry -> protocol core sync (add, join, replace, calibrate, remove -> UNPAIR)");
  FakeStore st; AquaRegistry<FakeStore> reg; reg.begin(&st);
  AquaGatewayCore core; core.begin(KEY, GWMAC, 7);
  int unpairs = 0; auto send = [&](const AquaGwOut& o) { (void)o; unpairs++; };
  const uint8_t rnd[8] = {5, 4, 3, 2, 1, 0, 9, 8};
  ManualNode m; testMac(0, m.mac); const char* code = "RPZ16DGPZA5Q";
  CHECK(reg.add(AQUA_DEV_FLOW, m.mac, code, "Kitchen", 1, rnd) == 1);
  aquaRegSyncCore(reg, core, send);
  CHECK(core.node(1).state == AquaGatewayCore::NS_PENDING && core.node(1).number == 1);
  aquaPairingKey(code, m.mac, m.kp);                                               // the node derives the same key from the sticker code
  uint32_t now = 1000; CHECK(m.join(core, now) && core.node(1).keyed && core.node(1).state == AquaGatewayCore::NS_PAIRED);
  reg.markPaired(1);

  reg.setCalibration(AQUA_DEV_FLOW, 1, 4321, AQUA_CFG_TX_REDUCE); aquaRegSyncCore(reg, core, send);
  CHECK(core.node(1).cfgPplX10 == 4321 && core.node(1).cfgFlags == AQUA_CFG_TX_REDUCE && core.node(1).keyed);       // config change keeps the session

  // reload from NVS (gateway reboot): the entry comes back as "paired" (needs one handshake per boot to get its key)
  { AquaRegistry<FakeStore> r2; r2.begin(&st); AquaGatewayCore c2; c2.begin(KEY, GWMAC, 8); aquaRegSyncCore(r2, c2, send);
    CHECK(c2.node(1).state == AquaGatewayCore::NS_PAIRED && !c2.node(1).keyed); ManualNode again = m; again.boot = 0xBEEF0009u; uint32_t t2 = 1000; CHECK(again.join(c2, t2)); }

  // replace: new hardware keeps slot + number, goes back to pending, the old node is no longer accepted
  ManualNode m2; testMac(1, m2.mac); const char* code2 = "ABCD-EFGH-JKMN"; reg.replace(AQUA_DEV_FLOW, 1, m2.mac, code2, rnd); aquaRegSyncCore(reg, core, send);
  CHECK(core.node(1).number == 1 && !memcmp(core.node(1).mac, m2.mac, 6) && core.node(1).state == AquaGatewayCore::NS_PENDING && !core.node(1).keyed);
  { char nc[13]; aquaCodeNormalize(code2, nc); aquaPairingKey(nc, m2.mac, m2.kp); } CHECK(m2.join(core, now));
  CHECK(!m.join(core, now));                                                       // the old node cannot join any more

  // remove while the node has a live session -> an UNPAIR frame goes out and the slot is free
  int before = unpairs; reg.remove(AQUA_DEV_FLOW, 1); aquaRegSyncCore(reg, core, send);
  CHECK(unpairs == before + 1 && core.node(1).state == AquaGatewayCore::NS_FREE && core.registeredCount() == 0);
  // a new node reuses the slot but gets a NEW number (numbers are never recycled to other hardware)
  ManualNode m3; testMac(2, m3.mac); CHECK(reg.add(AQUA_DEV_FLOW, m3.mac, "0123-4567-89AB", "", 2, rnd) == 1 && reg.at(1).number == 2);
  aquaRegSyncCore(reg, core, send); CHECK(core.node(1).number == 2 && core.node(1).state == AquaGatewayCore::NS_PENDING);
  // removing a node that never joined sends nothing
  before = unpairs; reg.remove(AQUA_DEV_FLOW, 2); aquaRegSyncCore(reg, core, send); CHECK(unpairs == before && core.registeredCount() == 0);
  // rain node numbering is independent
  ManualNode mr; testMac(3, mr.mac); CHECK(reg.add(AQUA_DEV_RAIN, mr.mac, "0123-4567-89AB", "", 2, rnd) > 0 && reg.at(reg.findByMac(mr.mac)).number == 1);
}

int main() {
  test_code();
  test_keys();
  test_qr();
  test_handshake();
  test_fake_gateway();
  test_per_node_keys();
  test_challenge_rules();
  test_nearby();
  test_registry();
  test_sim_join_and_unpair();
  test_sim_wrong_code_then_right_code();
  test_identity();
  test_regen_confirm();
  test_registry_sync();
  printf("\n%d checks passed, %d failed\n", g_pass, g_fail);
  return g_fail ? 1 : 0;
}
