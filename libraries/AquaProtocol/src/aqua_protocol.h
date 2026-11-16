/*
 * Aqua Guard ESP-NOW protocol, version 2.
 *
 * v2 (vs v1): registry-based pairing (no fixed node ids), battery voltage in the flow payload,
 * per-node data keys, UNPAIR frame, header flag AQUA_HF_RAIN selects the DATA payload layout.
 *
 * Shared, byte-packed definitions used by the ESP8266 flow nodes, the ESP32
 * rainwater node, the ESP32 gateway and the host unit tests.  Pure C++11,
 * no Arduino dependency, so it can be compiled and tested on a PC.
 *
 * Frame layout (all multi-byte fields little-endian):
 *   AquaHeader (14 bytes) | payload (payloadLen bytes) | tag (8 bytes)
 * The tag is a SipHash-2-4 MAC (64-bit) over header+payload using the 128-bit
 * pre-shared key AQUA_PSK.  It authenticates and integrity-protects EVERY
 * frame (including broadcast discovery), independent of ESP-NOW encryption.
 */
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>

#if defined(__BYTE_ORDER__) && (__BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__)
#error "Aqua protocol assumes a little-endian target"
#endif

#define AQUA_PROTO_MAGIC       0xA6
#define AQUA_PROTO_VERSION     2
#define AQUA_FW_VERSION        0x0200   // reported in discovery / heartbeat
#define AQUA_TAG_LEN           8
#define AQUA_MAX_FRAME         250      // ESP-NOW v1 payload limit

#ifndef AQUA_CH_MIN
#define AQUA_CH_MIN 1
#endif
#ifndef AQUA_CH_MAX
#define AQUA_CH_MAX 13                  // set 11 for FCC-only regions
#endif

// ---- identifiers -----------------------------------------------------------
// The header nodeId is a RADIO SLOT (1..AQUA_MAX_SLOTS) that the gateway registry hands out; it is an
// internal address and never shown to users (users see "Node N" / "Rain N", see the registry).
// Slot 0 = "no slot yet" (discovery and the pairing handshake).
#ifndef AQUA_MAX_SLOTS
#define AQUA_MAX_SLOTS          12
#endif

enum AquaDeviceType : uint8_t { AQUA_DEV_FLOW = 1, AQUA_DEV_RAIN = 2 };

enum AquaPacketType : uint8_t {
  AQUA_PKT_DISCOVERY_REQUEST  = 1,
  AQUA_PKT_DISCOVERY_RESPONSE = 2,
  AQUA_PKT_DATA               = 3,
  AQUA_PKT_ACK                = 4,
  AQUA_PKT_NACK               = 5,   // missing-sequence request
  AQUA_PKT_HEARTBEAT          = 6,
  // ---- pairing (nodeId 0 = "no slot yet"; PSK-tagged, authenticated by the pairing key Kp inside) ----
  AQUA_PKT_PAIR_ANNOUNCE      = 7,   // unpaired / re-joining device -> gateway
  AQUA_PKT_PAIR_CHALLENGE     = 8,   // gateway -> device (fresh 64-bit challenge)
  AQUA_PKT_PAIR_RESPONSE      = 9,   // device -> gateway: MAC(deviceSecret, challenge)
  AQUA_PKT_PAIR_ACCEPT        = 10,  // gateway -> device: slot + salt + proof the gateway knows the pairing key
  AQUA_PKT_UNPAIR             = 11   // gateway -> device: you were removed (tagged with the device's session key)
};

// header.flags
#define AQUA_HF_RETRANSMIT   0x01   // frame is a retransmission of an earlier seq
#define AQUA_HF_BACKLOG      0x02   // measurement was buffered while link was down
#define AQUA_HF_RAIN         0x04   // DATA payload is AquaRainPayload (else AquaFlowPayload)

#pragma pack(push, 1)
struct AquaHeader {
  uint8_t  magic;
  uint8_t  version;
  uint8_t  type;
  uint8_t  nodeId;
  uint32_t bootId;       // random per boot: identifies the session / counter epoch
  uint32_t seq;          // DATA: 1,2,3...  per boot.  Others: own counter
  uint8_t  flags;
  uint8_t  payloadLen;
};

struct AquaDiscoveryRequest {      // node -> broadcast
  uint32_t nonce;
  uint8_t  probeChannel;
  uint16_t fwVersion;
};

struct AquaDiscoveryResponse {     // gateway -> broadcast (authenticated by tag)
  uint32_t nonce;                  // echo of request nonce
  uint8_t  channel;                // gateway's actual Wi-Fi/ESP-NOW channel
  uint8_t  gwMac[6];               // gateway STA MAC (unicast target for the node)
  uint32_t gwBootId;
  int8_t   gwRssi;                 // gateway <-> AP RSSI (0 if unknown)
  uint8_t  gwFlags;                // bit0 wifi up, bit1 mqtt up
};

// Pairing handshake (identity = the device's radio MAC, authenticated by the pairing key Kp derived from
// the pairing code; the code itself is never transmitted):
//   node  -> ANNOUNCE            (no secret inside)
//   gw    -> CHALLENGE (unicast) 8 random bytes
//   node  -> RESPONSE            MAC_Kp('R', mac, boot, chal)
//   gw    -> ACCEPT (unicast)    slot + salt + MAC_Kp('A', mac, boot, chal, slot, salt)   (mutual proof)
// Both sides then derive Kn = KDF(Kp,'N',salt) and the per-boot data key Kop = KDF(Kn,'K',boot).
struct AquaPairAnnounce {          // device -> gateway.  Contains NO secret.
  uint8_t  devType;                // AquaDeviceType
  uint8_t  channel;
  uint16_t fwVersion;
};
struct AquaPairChallenge { uint8_t mac[6]; uint8_t chal[8]; };
struct AquaPairResponse  { uint8_t resp[8]; };
struct AquaPairAccept    { uint8_t mac[6]; uint8_t slot; uint8_t salt[8]; uint8_t proof[8]; };
struct AquaUnpair        { uint8_t reason; };

struct AquaFlowPayload {           // DATA from a flow node
  uint32_t intervalPulses;         // pulses counted in this interval
  uint32_t cumPulses;              // pulses since boot (mod 2^32) - PRIMARY volume record
  uint32_t flowMlPerMin;           // mL/min  (L/min x 1000), from real elapsed time
  uint32_t intervalMl;             // interval volume, mL
  uint32_t totalMl;                // cumulative volume since boot, mL (derived)
  uint32_t intervalMs;             // real measurement interval duration
  uint32_t uptimeS;
  uint16_t pulsesPerLiterX10;      // calibration constant x10 (450.0 -> 4500)
  uint16_t status;                 // AQUA_FS_* bits
  uint16_t batteryMv;              // battery voltage in mV, AQUA_BATT_NONE = not measured
};
#define AQUA_BATT_NONE 0xFFFF

struct AquaRainPayload {           // DATA from the rainwater node
  uint32_t uptimeS;
  uint16_t distanceMm;             // 0xFFFF = invalid (timeout / out of range)
  uint16_t levelX10;               // tank level % x10, 0xFFFF = invalid
  uint16_t turbidityAdc;           // raw 12-bit ADC
  uint16_t flags;                  // AQUA_RF_*
  uint16_t faults;                 // AQUA_RFAULT_*
  uint16_t rainEvents;             // rain events since boot
  uint8_t  valveState;             // AquaValveState
  uint8_t  valveReason;            // AquaValveReason
};

struct AquaAck {                   // gateway -> node
  uint32_t ackedSeq;               // seq just received (0 for heartbeat ack)
  uint32_t cumAck;                 // highest contiguous seq received (0 = none)
  uint32_t window;                 // bit i set => seq cumAck+1+i received
  uint8_t  ackedType;              // type being acknowledged
  uint8_t  gwFlags;                // bit0 wifi up, bit1 mqtt up
  uint16_t cfgPplX10;              // node configuration from the registry: pulses/L x10 (0 = not configured)
  uint8_t  cfgFlags;               // AQUA_CFG_* bits
};
#define AQUA_CFG_TX_REDUCE 0x01    // lower the radio TX power (weak/hot antennas, short range) - legacy bit, means 8.5 dBm
// Per-node TX power level in bits 1..3 of cfgFlags: 0 = not set (default 8.5 dBm), 1 = 8.5, 2 = 11, 3 = 13, 4 = 15 dBm.
// 8.5 is also written with the legacy bit set so older firmware still lowers the power.
#define AQUA_CFG_TXPWR_SHIFT 1
#define AQUA_CFG_TXPWR_MASK  0x0E
#define AQUA_CFG_KNOWN_MASK  (AQUA_CFG_TX_REDUCE | AQUA_CFG_TXPWR_MASK)
static inline uint8_t aquaTxCode(uint8_t f) { uint8_t c = (f & AQUA_CFG_TXPWR_MASK) >> AQUA_CFG_TXPWR_SHIFT; return c > 4 ? 1 : c; }
/** TX power in 0.1 dBm: 85, 110, 130 or 150 (default 85). */
static inline uint16_t aquaTxDbmX10(uint8_t f) { switch (aquaTxCode(f)) { case 2: return 110; case 3: return 130; case 4: return 150; default: return 85; } }
/** Same level as the ESP32 core's wifi_power_t value (quarter dBm): 34, 44, 52, 60. */
static inline int aquaTxQuarterDbm(uint8_t f) { return (int)(aquaTxDbmX10(f) * 4 / 10); }
/** cfgFlags for a requested power in 0.1 dBm; returns 0xFF when it is not one of the four allowed levels. */
static inline uint8_t aquaTxFlagsForDbmX10(uint16_t x10) {
  switch (x10) { case 85: return (uint8_t)((1 << AQUA_CFG_TXPWR_SHIFT) | AQUA_CFG_TX_REDUCE); case 110: return 2 << AQUA_CFG_TXPWR_SHIFT;
                 case 130: return 3 << AQUA_CFG_TXPWR_SHIFT; case 150: return 4 << AQUA_CFG_TXPWR_SHIFT; default: return 0xFF; }
}

#define AQUA_NACK_MAX 6
struct AquaNack {                  // gateway -> node: please resend these seqs
  uint8_t  count;
  uint32_t seqs[AQUA_NACK_MAX];
};

struct AquaHeartbeat {             // node -> gateway
  uint32_t uptimeS;
  uint32_t lastDataSeq;            // lets the gateway detect trailing loss
  uint16_t status;
  uint8_t  channel;
  uint16_t txRetransmits;
  uint16_t txDropped;              // frames abandoned after max attempts / evicted
  uint16_t rediscoveries;
  uint16_t fwVersion;
};
#pragma pack(pop)

static_assert(sizeof(AquaHeader) == 14, "header size");
static_assert(sizeof(AquaDiscoveryRequest) == 7, "disc req size");
static_assert(sizeof(AquaDiscoveryResponse) == 17, "disc resp size");
static_assert(sizeof(AquaPairAnnounce) == 4, "pair announce size");
static_assert(sizeof(AquaPairChallenge) == 14, "pair challenge size");
static_assert(sizeof(AquaPairResponse) == 8, "pair response size");
static_assert(sizeof(AquaPairAccept) == 23, "pair accept size");
static_assert(sizeof(AquaUnpair) == 1, "unpair size");
static_assert(sizeof(AquaFlowPayload) == 34, "flow payload size");
static_assert(sizeof(AquaRainPayload) == 18, "rain payload size");
static_assert(sizeof(AquaAck) == 17, "ack size");
static_assert(sizeof(AquaNack) == 25, "nack size");
static_assert(sizeof(AquaHeartbeat) == 19, "heartbeat size");
static_assert(sizeof(AquaHeader) + sizeof(AquaFlowPayload) + AQUA_TAG_LEN <= AQUA_MAX_FRAME, "fits ESP-NOW");
static_assert(sizeof(uint32_t) == 4 && sizeof(uint16_t) == 2, "fixed widths");

// AquaFlowPayload.status
#define AQUA_FS_OVER_RANGE   0x0001   // flow above sensor range (>35 L/min): noise/EMI?
#define AQUA_FS_STUCK_LOW    0x0002   // pulse pin held LOW with no pulses for >60 s
#define AQUA_FS_RATE_ANOMALY 0x0004   // pulse rate physically impossible
#define AQUA_FS_INTERVAL_JIT 0x0008   // interval deviated >50% from nominal

// AquaRainPayload.flags
#define AQUA_RF_RAIN          0x0001  // debounced rain
#define AQUA_RF_TANK_VALID    0x0002
#define AQUA_RF_TANK_FULL     0x0004
#define AQUA_RF_WATER_CLEAR   0x0008
#define AQUA_RF_VALVE_OPEN    0x0010  // valve commanded open
#define AQUA_RF_BUZZER        0x0020
#define AQUA_RF_FIRST_FLUSH   0x0040
#define AQUA_RF_TURB_VALID    0x0080
#define AQUA_RF_DRY_RUN       0x0100  // relay output disabled (software test mode)
#define AQUA_RF_LINK_DEGRADED 0x0200
#define AQUA_RF_OUT_OF_RANGE  0x0400  // ultrasonic hears nothing in front (no tank within range): not a sensor failure

// AquaRainPayload.faults
#define AQUA_RFAULT_ULTRASONIC     0x0001
#define AQUA_RFAULT_TURBIDITY      0x0002
#define AQUA_RFAULT_RAIN_SENSOR    0x0004
#define AQUA_RFAULT_RELAY          0x0008
#define AQUA_RFAULT_SENSOR_TIMEOUT 0x0010

enum AquaValveState : uint8_t {
  AQUA_VS_CLOSED_IDLE = 0, AQUA_VS_RAIN_DEBOUNCE = 1, AQUA_VS_FIRST_FLUSH = 2,
  AQUA_VS_OPEN_COLLECTING = 3, AQUA_VS_CLOSED_TANK_FULL = 4,
  AQUA_VS_CLOSED_UNSAFE = 5, AQUA_VS_FAULT_SAFE = 6, AQUA_VS_RAIN_ENDING = 7
};

// ---- SipHash-2-4 -----------------------------------------------------------
static inline uint64_t aquaRotl64(uint64_t x, int b) { return (x << b) | (x >> (64 - b)); }

#define AQUA_SIPROUND \
  v0 += v1; v1 = aquaRotl64(v1, 13); v1 ^= v0; v0 = aquaRotl64(v0, 32); \
  v2 += v3; v3 = aquaRotl64(v3, 16); v3 ^= v2; \
  v0 += v3; v3 = aquaRotl64(v3, 21); v3 ^= v0; \
  v2 += v1; v1 = aquaRotl64(v1, 17); v1 ^= v2; v2 = aquaRotl64(v2, 32);

static inline uint64_t aquaSipHash24(const uint8_t key[16], const uint8_t* in, size_t inlen) {
  uint64_t k0, k1;
  memcpy(&k0, key, 8); memcpy(&k1, key + 8, 8);
  uint64_t v0 = 0x736f6d6570736575ULL ^ k0, v1 = 0x646f72616e646f6dULL ^ k1;
  uint64_t v2 = 0x6c7967656e657261ULL ^ k0, v3 = 0x7465646279746573ULL ^ k1;
  const uint8_t* end = in + (inlen - (inlen % 8));
  for (; in != end; in += 8) {
    uint64_t m; memcpy(&m, in, 8);
    v3 ^= m; AQUA_SIPROUND AQUA_SIPROUND v0 ^= m;
  }
  uint64_t b = ((uint64_t)inlen) << 56;
  for (size_t i = 0; i < (inlen % 8); i++) b |= ((uint64_t)in[i]) << (8 * i);
  v3 ^= b; AQUA_SIPROUND AQUA_SIPROUND v0 ^= b;
  v2 ^= 0xff; AQUA_SIPROUND AQUA_SIPROUND AQUA_SIPROUND AQUA_SIPROUND
  return v0 ^ v1 ^ v2 ^ v3;
}
#undef AQUA_SIPROUND

// ---- build / parse ---------------------------------------------------------
enum AquaRc : int8_t {
  AQUA_OK = 0, AQUA_E_LEN = -1, AQUA_E_MAGIC = -2, AQUA_E_VERSION = -3, AQUA_E_TYPE = -4,
  AQUA_E_NODE = -5, AQUA_E_PLEN = -6, AQUA_E_TAG = -7, AQUA_E_VALUE = -8
};

static inline int aquaExpectedPayloadLen(uint8_t type, uint8_t flags) {
  switch (type) {
    case AQUA_PKT_PAIR_ANNOUNCE:      return (int)sizeof(AquaPairAnnounce);
    case AQUA_PKT_PAIR_CHALLENGE:     return (int)sizeof(AquaPairChallenge);
    case AQUA_PKT_PAIR_RESPONSE:      return (int)sizeof(AquaPairResponse);
    case AQUA_PKT_PAIR_ACCEPT:        return (int)sizeof(AquaPairAccept);
    case AQUA_PKT_UNPAIR:             return (int)sizeof(AquaUnpair);
    case AQUA_PKT_DISCOVERY_REQUEST:  return (int)sizeof(AquaDiscoveryRequest);
    case AQUA_PKT_DISCOVERY_RESPONSE: return (int)sizeof(AquaDiscoveryResponse);
    case AQUA_PKT_DATA:               return (flags & AQUA_HF_RAIN) ? (int)sizeof(AquaRainPayload) : (int)sizeof(AquaFlowPayload);
    case AQUA_PKT_ACK:                return (int)sizeof(AquaAck);
    case AQUA_PKT_NACK:               return (int)sizeof(AquaNack);
    case AQUA_PKT_HEARTBEAT:          return (int)sizeof(AquaHeartbeat);
    default: return -1;
  }
}

// Returns total frame length.
static inline size_t aquaBuildFrame(uint8_t* out, uint8_t type, uint8_t nodeId, uint32_t bootId,
                                    uint32_t seq, uint8_t flags, const void* payload,
                                    uint8_t payloadLen, const uint8_t key[16]) {
  AquaHeader h;
  h.magic = AQUA_PROTO_MAGIC; h.version = AQUA_PROTO_VERSION; h.type = type; h.nodeId = nodeId;
  h.bootId = bootId; h.seq = seq; h.flags = flags; h.payloadLen = payloadLen;
  memcpy(out, &h, sizeof(h));
  if (payloadLen) memcpy(out + sizeof(h), payload, payloadLen);
  size_t body = sizeof(h) + payloadLen;
  uint64_t tag = aquaSipHash24(key, out, body);
  memcpy(out + body, &tag, AQUA_TAG_LEN);
  return body + AQUA_TAG_LEN;
}

// Which header nodeId values are legal for a packet type.
static inline bool aquaNodeIdOk(uint8_t type, uint8_t node) {
  switch (type) {
    case AQUA_PKT_DISCOVERY_REQUEST:
    case AQUA_PKT_DISCOVERY_RESPONSE:
    case AQUA_PKT_PAIR_ANNOUNCE:
    case AQUA_PKT_PAIR_CHALLENGE:
    case AQUA_PKT_PAIR_RESPONSE:
    case AQUA_PKT_PAIR_ACCEPT:        return node == 0;                       // no slot yet
    default:                          return node >= 1 && node <= AQUA_MAX_SLOTS;
  }
}

// Structure only (no tag check).  Use when the verification key depends on the header (per-slot keys).
static inline AquaRc aquaParseStruct(const uint8_t* buf, size_t len, AquaHeader* hdr, const uint8_t** payload) {
  if (len < sizeof(AquaHeader) + AQUA_TAG_LEN || len > AQUA_MAX_FRAME) return AQUA_E_LEN;
  AquaHeader h; memcpy(&h, buf, sizeof(h));
  if (h.magic != AQUA_PROTO_MAGIC) return AQUA_E_MAGIC;
  if (h.version != AQUA_PROTO_VERSION) return AQUA_E_VERSION;
  if (h.type < AQUA_PKT_DISCOVERY_REQUEST || h.type > AQUA_PKT_UNPAIR) return AQUA_E_TYPE;
  if (!aquaNodeIdOk(h.type, h.nodeId)) return AQUA_E_NODE;
  if (len != sizeof(AquaHeader) + (size_t)h.payloadLen + AQUA_TAG_LEN) return AQUA_E_LEN;
  int exp = aquaExpectedPayloadLen(h.type, h.flags);
  if (exp < 0 || h.payloadLen != (uint8_t)exp) return AQUA_E_PLEN;
  *hdr = h; *payload = buf + sizeof(AquaHeader);
  return AQUA_OK;
}

static inline bool aquaVerifyTag(const uint8_t* buf, size_t len, const uint8_t key[16]) {
  uint64_t got; memcpy(&got, buf + len - AQUA_TAG_LEN, AQUA_TAG_LEN);
  uint64_t want = aquaSipHash24(key, buf, len - AQUA_TAG_LEN);
  return (got ^ want) == 0;
}

// Validates structure + authenticity with ONE key.  On AQUA_OK, *hdr is filled and
// *payload points INTO buf (not necessarily aligned: memcpy before use).
static inline AquaRc aquaParseFrame(const uint8_t* buf, size_t len, const uint8_t key[16],
                                    AquaHeader* hdr, const uint8_t** payload) {
  AquaRc rc = aquaParseStruct(buf, len, hdr, payload);
  if (rc != AQUA_OK) return rc;
  return aquaVerifyTag(buf, len, key) ? AQUA_OK : AQUA_E_TAG;
}

// ---- key hierarchy (all SipHash-2-4; see docs/PAIRING.md and aqua_join.h) ---------------
//   PSK  : per-site key compiled into every device. Tags ONLY discovery + the pairing handshake frames.
//   Kp   : pairing key   = f(pairing code, node MAC)        - known to that node and (from the QR/manual entry) the gateway.
//   Kn   : per-node key  = KDF(Kp,'N',salt)                  - salt is chosen by the gateway at registration.
//   Kop  : per-boot key  = KDF(Kn,'K',0,bootId)              - tags DATA/ACK/NACK/HEARTBEAT/UNPAIR of that node.
//   Kcmd : command key   = KDF(site command key,'C',0,0)    - authenticates backend<->gateway messages.
static inline void aquaKdf(const uint8_t key[16], char label, uint32_t a, uint32_t b, uint8_t out[16]) {
  uint8_t in[10]; in[0] = (uint8_t)label; memcpy(in + 1, &a, 4); memcpy(in + 5, &b, 4);
  in[9] = 0; uint64_t h0 = aquaSipHash24(key, in, sizeof(in));
  in[9] = 1; uint64_t h1 = aquaSipHash24(key, in, sizeof(in));
  memcpy(out, &h0, 8); memcpy(out + 8, &h1, 8);
}
static inline bool aquaCtEq(const uint8_t* a, const uint8_t* b, size_t n) {
  uint8_t d = 0; for (size_t i = 0; i < n; i++) d |= (uint8_t)(a[i] ^ b[i]);
  return d == 0;
}

// ---- measurement sanity checks (reject physically impossible data) ----------
static inline AquaRc aquaValidateFlow(const AquaFlowPayload& p) {
  if (p.pulsesPerLiterX10 < 100 || p.pulsesPerLiterX10 > 60000) return AQUA_E_VALUE;
  if (p.intervalMs < 50 || p.intervalMs > 3600000UL) return AQUA_E_VALUE;
  if (p.flowMlPerMin > 120000UL) return AQUA_E_VALUE;   // >120 L/min impossible for a YF-S201
  if (p.intervalPulses > 1000000UL) return AQUA_E_VALUE;
  if (p.batteryMv != AQUA_BATT_NONE && p.batteryMv > 20000) return AQUA_E_VALUE;
  return AQUA_OK;
}
static inline AquaRc aquaValidateRain(const AquaRainPayload& p) {
  if (p.levelX10 != 0xFFFF && p.levelX10 > 1000) return AQUA_E_VALUE;
  if (p.distanceMm != 0xFFFF && p.distanceMm > 5000) return AQUA_E_VALUE;
  if (p.turbidityAdc > 4095) return AQUA_E_VALUE;
  if (p.valveState > AQUA_VS_RAIN_ENDING) return AQUA_E_VALUE;
  return AQUA_OK;
}
