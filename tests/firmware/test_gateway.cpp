// Host tests: ChaCha20 vectors, gateway registry (persistence, numbering, replace), signed AQC2 commands.
//   clang++ -std=c++17 -Ilibraries/AquaProtocol/src -Itests/firmware tests/firmware/test_gateway.cpp -o test_gateway
#include <cstdio>
#include <cstdlib>
#include <map>
#include <string>
#include "AquaProtocol.h"
#include "aqua_registry.h"

static int g_pass = 0, g_fail = 0;
#define CHECK(c) do { if (c) { g_pass++; } else { g_fail++; printf("  FAIL %s:%d  %s\n", __FILE__, __LINE__, #c); } } while (0)
#define SECTION(n) printf("[%s]\n", n)

struct FakeStore {
  std::map<std::string, std::string> m;
  bool getStr(const char* k, char* out, size_t cap) { auto i = m.find(k); if (i == m.end() || i->second.size() + 1 > cap) return false; strcpy(out, i->second.c_str()); return true; }
  void putStr(const char* k, const char* v) { m[k] = v; }
  uint8_t getU8(const char* k, uint8_t d) { auto i = m.find(k); return i == m.end() ? d : (uint8_t)atoi(i->second.c_str()); }
  void putU8(const char* k, uint8_t v) { m[k] = std::to_string(v); }
  void remove(const char* k) { m.erase(k); }
};

static const uint8_t SITE[16] = {0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88, 0x99, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xF0, 0x01};
static const uint8_t NONCE_A[12] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12};
static const uint8_t RND[8] = {9, 9, 9, 9, 9, 9, 9, 9};
static uint8_t macN(uint8_t i) { return i; }
static void mkMac(uint8_t i, uint8_t out[6]) { out[0] = 0x44; out[1] = 0x7B; out[2] = 0x30; out[3] = 0x85; out[4] = 0x4B; out[5] = macN(i); }
static std::string hexs(const uint8_t* p, size_t n) { char b[3]; std::string s; for (size_t i = 0; i < n; i++) { snprintf(b, 3, "%02x", p[i]); s += b; } return s; }

static void test_chacha() {
  SECTION("ChaCha20 (RFC 8439 vectors)");
  uint8_t key[32]; for (int i = 0; i < 32; i++) key[i] = i;
  const uint8_t n1[12] = {0, 0, 0, 9, 0, 0, 0, 0x4a, 0, 0, 0, 0};
  uint8_t blk[64]; aquaChaChaBlock(key, 1, n1, blk);                                    // RFC 8439 section 2.3.2
  CHECK(hexs(blk, 16) == "10f1e7e4d13b5915500fdd1fa32071c4");
  const uint8_t n2[12] = {0, 0, 0, 0, 0, 0, 0, 0x4a, 0, 0, 0, 0};
  const char* pt = "Ladies and Gentlemen of the class of '99: If I could offer you only one tip for the future, sunscreen would be it.";
  uint8_t buf[200]; size_t len = strlen(pt); memcpy(buf, pt, len);
  aquaChaChaXor(key, n2, 1, buf, len);                                                  // section 2.4.2 (spans 2 blocks)
  CHECK(len == 114);
  CHECK(hexs(buf, 16) == "6e2e359a2568f98041ba0728dd0d6981");
  aquaChaChaXor(key, n2, 1, buf, len);
  CHECK(memcmp(buf, pt, len) == 0);                                                     // symmetric
}

static void test_registry() {
  SECTION("registry: numbering, persistence, duplicates, replace");
  FakeStore st; AquaRegistry<FakeStore> r; r.begin(&st);
  uint8_t m1[6], m2[6], m3[6], mr[6]; mkMac(1, m1); mkMac(2, m2); mkMac(3, m3); mkMac(9, mr);
  CHECK(r.add(AQUA_DEV_FLOW, m1, "rpz1-6dgp-za5q", "Kitchen", 1700000000, RND) == 1);
  CHECK(r.add(AQUA_DEV_FLOW, m2, "ABCD-EFGH-JKMN", "", 1700000001, RND) == 2);
  CHECK(r.add(AQUA_DEV_RAIN, mr, "0123-4567-89AB", "Tank", 1700000002, RND) == 3);
  CHECK(r.at(1).number == 1 && r.at(2).number == 2 && r.at(3).number == 1);               // numbers are per type
  CHECK(strcmp(r.at(2).name, "Node 2") == 0 && strcmp(r.at(1).name, "Kitchen") == 0);
  CHECK(strcmp(r.at(1).code, "RPZ16DGPZA5Q") == 0 && r.at(1).state == 1);
  CHECK(r.add(AQUA_DEV_FLOW, m1, "ABCD-EFGH-JKMN", "x", 1, RND) == AQUA_REG_DUP_MAC);   // duplicate MAC refused
  CHECK(r.add(AQUA_DEV_FLOW, m3, "ABCD-EFGH-JKM", "x", 1, RND) == AQUA_REG_BAD_CODE);
  CHECK(r.add(AQUA_DEV_FLOW, m3, "ABCD-EFGH-JKMU", "x", 1, RND) == AQUA_REG_BAD_CODE);
  CHECK(r.add(7, m3, "ABCD-EFGH-JKMN", "x", 1, RND) == AQUA_REG_BAD_ARG);
  r.markPaired(1); CHECK(r.at(1).state == 2);

  { AquaRegistry<FakeStore> r2; r2.begin(&st);                                          // reboot: everything is restored
    CHECK(r2.count() == 3 && r2.at(1).state == 2 && !memcmp(r2.at(1).mac, m1, 6) && strcmp(r2.at(1).code, "RPZ16DGPZA5Q") == 0);
    CHECK(r2.at(3).devType == AQUA_DEV_RAIN && strcmp(r2.at(3).name, "Tank") == 0 && r2.at(1).addedEpoch == 1700000000); }

  CHECK(r.remove(AQUA_DEV_FLOW, 2) == 2 && r.count() == 2);
  CHECK(r.add(AQUA_DEV_FLOW, m3, "ABCD-EFGH-JKMN", "", 5, RND) == 2);                    // the slot is reused ...
  CHECK(r.at(2).number == 3);                                                            // ... but Node 2 is NOT handed to a different MAC
  { AquaRegistry<FakeStore> r3; r3.begin(&st); CHECK(r3.at(2).number == 3); r3.remove(AQUA_DEV_FLOW, 3); r3.add(AQUA_DEV_FLOW, m2, "ABCD-EFGH-JKMN", "", 5, RND); CHECK(r3.at(2).number == 4); }

  uint8_t m4[6]; mkMac(4, m4);
  CHECK(r.replace(AQUA_DEV_FLOW, 1, m4, "ZZZZ-ZZZZ-ZZZZ", RND) == 1);                    // replace keeps slot + number
  CHECK(r.at(1).number == 1 && !memcmp(r.at(1).mac, m4, 6) && r.at(1).state == 1 && strcmp(r.at(1).name, "Kitchen") == 0);
  CHECK(r.replace(AQUA_DEV_FLOW, 1, m3, "ZZZZ-ZZZZ-ZZZZ", RND) == AQUA_REG_DUP_MAC);     // new MAC already used by another node
  CHECK(r.replace(AQUA_DEV_FLOW, 55, m3, "ZZZZ-ZZZZ-ZZZZ", RND) == AQUA_REG_NOT_FOUND);

  CHECK(r.rename(AQUA_DEV_FLOW, 1, "Bath | room\x01") > 0 && strcmp(r.at(1).name, "Bath  room") == 0);   // '|' and control chars removed
  CHECK(r.rename(AQUA_DEV_FLOW, 1, "") == AQUA_REG_BAD_ARG);
  CHECK(r.setCalibration(AQUA_DEV_FLOW, 1, 4321, 1) > 0 && r.at(1).pplX10 == 4321 && r.at(1).cfgFlags == 1);
  // per-node TX power: levels 8.5 / 11 / 13 / 15 dBm live in cfgFlags bits 1..3 and survive the registry
  CHECK(aquaTxDbmX10(0) == 85 && aquaTxQuarterDbm(0) == 34);                              // not set = default 8.5 dBm
  CHECK(aquaTxDbmX10(AQUA_CFG_TX_REDUCE) == 85);                                          // legacy bit = 8.5
  CHECK(aquaTxFlagsForDbmX10(85) == 0x03 && aquaTxFlagsForDbmX10(110) == 0x04 && aquaTxFlagsForDbmX10(130) == 0x06 && aquaTxFlagsForDbmX10(150) == 0x08);
  CHECK(aquaTxFlagsForDbmX10(100) == 0xFF && aquaTxFlagsForDbmX10(195) == 0xFF && aquaTxFlagsForDbmX10(0) == 0xFF);   // only the four levels
  CHECK(aquaTxQuarterDbm(0x04) == 44 && aquaTxQuarterDbm(0x06) == 52 && aquaTxQuarterDbm(0x08) == 60);
  CHECK(aquaTxDbmX10(0x0A) == 85 && aquaTxDbmX10(0x0E) == 85);                            // codes 5..7 are invalid: fall back to the default
  for (uint16_t lvl : {85, 110, 130, 150}) { uint8_t f = aquaTxFlagsForDbmX10(lvl); CHECK(r.setCalibration(AQUA_DEV_FLOW, 1, 4500, f) > 0 && r.at(1).cfgFlags == f && aquaTxDbmX10(r.at(1).cfgFlags) == lvl); }
  CHECK(r.setCalibration(AQUA_DEV_FLOW, 1, 4500, 0xF1) > 0 && (r.at(1).cfgFlags & ~AQUA_CFG_KNOWN_MASK) == 0);   // unknown bits are dropped
  CHECK(r.setCalibration(AQUA_DEV_FLOW, 1, 50, 0) == AQUA_REG_BAD_ARG);                  // 5 pulses/L is not a flow meter
  CHECK(r.setCalibration(AQUA_DEV_FLOW, 1, 0, 0) > 0);                                   // 0 = back to node default
  { AquaRegistry<FakeStore> r4; r4.begin(&st); CHECK(strcmp(r4.at(1).name, "Bath  room") == 0 && r4.at(1).pplX10 == 0); }

  st.m["n1"] = "garbage"; { AquaRegistry<FakeStore> r5; r5.begin(&st); CHECK(!r5.at(1).used); }   // corrupt entry is ignored, not fatal

  AquaRegistry<FakeStore> full; FakeStore st2; full.begin(&st2);                         // capacity = AQUA_MAX_SLOTS
  for (int i = 0; i < AQUA_MAX_SLOTS; i++) { uint8_t m[6]; mkMac(100 + i, m); CHECK(full.add(AQUA_DEV_FLOW, m, "ABCD-EFGH-JKMN", "", 1, RND) > 0); }
  { uint8_t m[6]; mkMac(200, m); CHECK(full.add(AQUA_DEV_FLOW, m, "ABCD-EFGH-JKMN", "", 1, RND) == AQUA_REG_FULL); }
  full.clearAll(); CHECK(full.count() == 0 && st2.m.find("n1") == st2.m.end());
  { uint8_t m[6]; mkMac(1, m); CHECK(full.add(AQUA_DEV_FLOW, m, "ABCD-EFGH-JKMN", "", 1, RND) == 1 && full.at(1).number == 1); }   // factory reset restarts numbering

  // the registry drives the protocol core: every stored entry becomes a registered node with the key derived from its code
  AquaGatewayCore core; const uint8_t psk[16] = {1, 2, 3}, gw[6] = {2, 0, 0, 0, 0, 1}; core.begin(psk, gw, 1);
  for (uint8_t s = 1; s <= AQUA_MAX_SLOTS; s++) if (r.at(s).used) { uint8_t kp[16]; aquaPairingKey(r.at(s).code, r.at(s).mac, kp);
    CHECK(core.registerNode(s, r.at(s).number, r.at(s).devType, r.at(s).mac, kp, r.at(s).salt, r.at(s).state == 2)); }
  CHECK(core.registeredCount() == r.count());
}

static const uint32_t NOW = 1760000000;

static void test_cmd2() {
  SECTION("signed command AQC2: tag, replay, time, encrypted code");
  char line[256]; uint8_t mac[6]; mkMac(5, mac); AquaCmd2 c; AquaCmd2Guard g;
  int n = aquaCmd2Build(line, sizeof(line), SITE, NOW, NONCE_A, 'A', AQUA_DEV_FLOW, 0, mac, -1, 0, "Kitchen", "RPZ16DGPZA5Q");
  CHECK(n > 0);
  CHECK(strcmp(line, "AQC2 1760000000 0102030405060708090a0b0c A 1 0 447b30854b05 - - 4b69746368656e 981486ee193c208ef38faa4f cb38fa4b2383718d") == 0);   // = tests/cmd2_vectors.json (the backend must build the same line)
  CHECK(strstr(line, hexs((const uint8_t*)"RPZ16DGPZA5Q", 12).c_str()) == nullptr);       // the code is NOT in the clear
  CHECK(strstr(line, "RPZ1") == nullptr && strstr(line, "Kitchen") == nullptr);
  CHECK(aquaCmd2Parse(line, n, SITE, NOW + 5, g, &c) == AQUA_C2_OK);
  CHECK(c.op == 'A' && c.devType == AQUA_DEV_FLOW && c.hasMac && !memcmp(c.mac, mac, 6) && c.hasCode && strcmp(c.code, "RPZ16DGPZA5Q") == 0 && strcmp(c.name, "Kitchen") == 0);
  CHECK(aquaCmd2Parse(line, n, SITE, NOW + 5, g, &c) == AQUA_C2_REPLAY);                // same message again
  { AquaCmd2Guard g2; uint8_t other[16]; memcpy(other, SITE, 16); other[0] ^= 1;
    CHECK(aquaCmd2Parse(line, n, other, NOW, g2, &c) == AQUA_C2_TAG); }                  // wrong site key
  { AquaCmd2Guard g2; char bad[256]; strcpy(bad, line); bad[strlen(bad) - 20] ^= 1;     // flip a bit inside the body or the tag
    CHECK(aquaCmd2Parse(bad, strlen(bad), SITE, NOW, g2, &c) == AQUA_C2_TAG); }
  { AquaCmd2Guard g2; char bad[256]; strcpy(bad, line); char* p = strstr(bad, " 1 0 "); p[1] = '2';        // change the device type: tag must break
    CHECK(aquaCmd2Parse(bad, strlen(bad), SITE, NOW, g2, &c) == AQUA_C2_TAG); }
  { AquaCmd2Guard g2; CHECK(aquaCmd2Parse(line, n, SITE, 0, g2, &c) == AQUA_C2_NO_TIME); }                // gateway time not synced: fail closed
  { AquaCmd2Guard g2; CHECK(aquaCmd2Parse(line, n, SITE, NOW + 301, g2, &c) == AQUA_C2_STALE); }          // too old
  { AquaCmd2Guard g2; CHECK(aquaCmd2Parse(line, n, SITE, NOW - 301, g2, &c) == AQUA_C2_STALE); }          // from the future
  { AquaCmd2Guard g2; CHECK(aquaCmd2Parse(line, n, SITE, NOW + 300, g2, &c) == AQUA_C2_OK); }             // inside the window
  { AquaCmd2Guard g2; CHECK(aquaCmd2Parse(line, n - 1, SITE, NOW, g2, &c) != AQUA_C2_OK); CHECK(aquaCmd2Parse("", 0, SITE, NOW, g2, &c) == AQUA_C2_FORMAT);
    CHECK(aquaCmd2Parse("AQC1 1 2 3", 10, SITE, NOW, g2, &c) == AQUA_C2_FORMAT); }

  // ordering: an older timestamp is refused after a newer command was accepted; a new nonce at the same ts is fine
  AquaCmd2Guard go; char l2[256], l3[256], l4[256]; uint8_t nB[12], nC[12], nD[12]; memcpy(nB, NONCE_A, 12); memcpy(nC, NONCE_A, 12); memcpy(nD, NONCE_A, 12); nB[0] = 0xB0; nC[0] = 0xC0; nD[0] = 0xD0;
  int n2 = aquaCmd2Build(l2, sizeof(l2), SITE, NOW + 10, nB, 'R', AQUA_DEV_FLOW, 3, nullptr, -1, 0, "", nullptr);
  int n3 = aquaCmd2Build(l3, sizeof(l3), SITE, NOW + 10, nC, 'N', AQUA_DEV_FLOW, 3, nullptr, -1, 0, "Garden", nullptr);
  int n4 = aquaCmd2Build(l4, sizeof(l4), SITE, NOW + 5, nD, 'C', AQUA_DEV_FLOW, 3, nullptr, 4321, 1, "", nullptr);
  CHECK(aquaCmd2Parse(l2, n2, SITE, NOW + 10, go, &c) == AQUA_C2_OK && c.op == 'R' && c.number == 3 && !c.hasCode);
  CHECK(aquaCmd2Parse(l3, n3, SITE, NOW + 10, go, &c) == AQUA_C2_OK && c.op == 'N' && strcmp(c.name, "Garden") == 0);
  CHECK(aquaCmd2Parse(l4, n4, SITE, NOW + 10, go, &c) == AQUA_C2_STALE);                  // older than the last accepted command

  // field rules per operation
  { AquaCmd2Guard g2; char l[256]; uint8_t nn[12] = {7};
    int k = aquaCmd2Build(l, sizeof(l), SITE, NOW, nn, 'A', AQUA_DEV_FLOW, 0, nullptr, -1, 0, "x", "RPZ16DGPZA5Q");        // add without a MAC
    CHECK(aquaCmd2Parse(l, k, SITE, NOW, g2, &c) == AQUA_C2_FIELD);
    k = aquaCmd2Build(l, sizeof(l), SITE, NOW, nn, 'R', AQUA_DEV_FLOW, 0, nullptr, -1, 0, "", nullptr);                   // remove without a number
    CHECK(aquaCmd2Parse(l, k, SITE, NOW, g2, &c) == AQUA_C2_FIELD);
    k = aquaCmd2Build(l, sizeof(l), SITE, NOW, nn, 'A', AQUA_DEV_FLOW, 0, mac, -1, 0, "x", "ABCD-EFGH-JKM");                // not 12 symbols -> builder encrypts what it is given
    CHECK(aquaCmd2Parse(l, k, SITE, NOW, g2, &c) != AQUA_C2_OK);
    k = aquaCmd2Build(l, sizeof(l), SITE, NOW, nn, 'A', AQUA_DEV_FLOW, 0, mac, -1, 0, "x", "ABCDEFGHJKMU");                 // U is not a code symbol
    CHECK(aquaCmd2Parse(l, k, SITE, NOW, g2, &c) == AQUA_C2_FIELD); }

  // command -> registry
  FakeStore st; AquaRegistry<FakeStore> reg; reg.begin(&st); AquaCmd2Guard gg; char l[256]; uint8_t nn[12] = {1}; uint8_t mA[6], mB[6]; mkMac(11, mA); mkMac(12, mB);
  nn[0] = 1; int k = aquaCmd2Build(l, sizeof(l), SITE, NOW, nn, 'A', AQUA_DEV_FLOW, 0, mA, -1, 0, "Kitchen", "RPZ16DGPZA5Q");
  CHECK(aquaCmd2Parse(l, k, SITE, NOW, gg, &c) == AQUA_C2_OK && aquaCmd2Apply(reg, c, NOW, RND) == 1 && reg.at(1).number == 1);
  nn[0] = 2; k = aquaCmd2Build(l, sizeof(l), SITE, NOW + 1, nn, 'C', AQUA_DEV_FLOW, 1, nullptr, 4400, 1, "", nullptr);
  CHECK(aquaCmd2Parse(l, k, SITE, NOW + 1, gg, &c) == AQUA_C2_OK && aquaCmd2Apply(reg, c, NOW, RND) == 1 && reg.at(1).pplX10 == 4400);
  nn[0] = 3; k = aquaCmd2Build(l, sizeof(l), SITE, NOW + 2, nn, 'P', AQUA_DEV_FLOW, 1, mB, -1, 0, "", "ABCDEFGHJKMN");
  CHECK(aquaCmd2Parse(l, k, SITE, NOW + 2, gg, &c) == AQUA_C2_OK && aquaCmd2Apply(reg, c, NOW, RND) == 1 && !memcmp(reg.at(1).mac, mB, 6) && reg.at(1).number == 1);
  nn[0] = 4; k = aquaCmd2Build(l, sizeof(l), SITE, NOW + 3, nn, 'R', AQUA_DEV_FLOW, 1, nullptr, -1, 0, "", nullptr);
  CHECK(aquaCmd2Parse(l, k, SITE, NOW + 3, gg, &c) == AQUA_C2_OK && aquaCmd2Apply(reg, c, NOW, RND) == 1 && reg.count() == 0);
  nn[0] = 5; k = aquaCmd2Build(l, sizeof(l), SITE, NOW + 4, nn, 'R', AQUA_DEV_FLOW, 1, nullptr, -1, 0, "", nullptr);
  CHECK(aquaCmd2Parse(l, k, SITE, NOW + 4, gg, &c) == AQUA_C2_OK && aquaCmd2Apply(reg, c, NOW, RND) == AQUA_REG_NOT_FOUND);
}

int main() {
  test_chacha();
  test_registry();
  test_cmd2();
  printf("\n%d checks passed, %d failed\n", g_pass, g_fail);
  return g_fail ? 1 : 0;
}
