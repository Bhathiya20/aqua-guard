/*
 * MQTT JSON telemetry formatting (schema 3).  Pure C, host-testable, uses
 * integer fixed-point so output is identical on ESP32 and PC.
 *
 * Schema 3 vs 2: "node" is the user-visible NUMBER inside the node's type (Node N / Rain N) assigned by the
 * gateway registry, "type" and "mac" identify the hardware, flow nodes add "batteryMv" (null if not measured).
 * The radio slot is internal and never published.
 *
 * Topic layout (prefix = site topic prefix, default waterleak/project001; <gw> = gateway id):
 *   <prefix>/node<N>                 flow telemetry (legacy-compatible topic, kept)
 *   <prefix>/<gw>/node/<N>           flow telemetry (new, per gateway)
 *   <prefix>/rainwater               rainwater telemetry (topic unchanged)
 *   <prefix>/gateway/heartbeat       gateway health JSON, every 15 s
 *   <prefix>/gateway/status          "online" | "offline" (retained, also the Last Will)
 */
#pragma once
#include <stdio.h>
#include <stdint.h>
#include "aqua_protocol.h"
#include "aqua_gateway_core.h"
#include "aqua_join.h"

#define AQUA_JSON_SCHEMA 3

static inline int aquaJsonFlow(char* out, size_t cap, const AquaTelemetry& t) {
  AquaFlowPayload f; memcpy(&f, t.payload, sizeof(f));
  char mac[18]; aquaMacFormat(t.mac, mac);
  char batt[8]; if (f.batteryMv == AQUA_BATT_NONE) snprintf(batt, sizeof(batt), "null"); else snprintf(batt, sizeof(batt), "%u", f.batteryMv);
  return snprintf(out, cap,
    "{\"schema\":%d,\"node\":%u,\"type\":\"flow\",\"mac\":\"%s\",\"boot\":%lu,\"seq\":%lu,\"recovered\":%s,\"backlog\":%s,"
    "\"flowRate\":%lu.%03lu,\"totalLiters\":%lu.%03lu,\"intervalLiters\":%lu.%03lu,"
    "\"intervalPulses\":%lu,\"cumPulses\":%lu,\"pulsesPerLiter\":%u.%u,\"intervalMs\":%lu,\"batteryMv\":%s,"
    "\"uptime\":%lu,\"status\":%u,\"rxEpoch\":%lu,\"gwUptimeMs\":%lu,\"channel\":%u,\"rssi\":%d}",
    AQUA_JSON_SCHEMA, t.number, mac, (unsigned long)t.bootId, (unsigned long)t.seq,
    (t.flags & AQUA_TF_RECOVERED) ? "true" : "false", (t.flags & AQUA_TF_BACKLOG) ? "true" : "false",
    (unsigned long)(f.flowMlPerMin / 1000), (unsigned long)(f.flowMlPerMin % 1000),
    (unsigned long)(f.totalMl / 1000), (unsigned long)(f.totalMl % 1000),
    (unsigned long)(f.intervalMl / 1000), (unsigned long)(f.intervalMl % 1000),
    (unsigned long)f.intervalPulses, (unsigned long)f.cumPulses,
    f.pulsesPerLiterX10 / 10, f.pulsesPerLiterX10 % 10, (unsigned long)f.intervalMs, batt,
    (unsigned long)f.uptimeS, f.status, (unsigned long)t.rxEpoch, (unsigned long)t.rxUptimeMs,
    t.channel, (int)t.rssi);
}

static inline int aquaJsonRain(char* out, size_t cap, const AquaTelemetry& t) {
  AquaRainPayload r; memcpy(&r, t.payload, sizeof(r));
  char mac[18]; aquaMacFormat(t.mac, mac);
  bool levelOk = r.levelX10 != 0xFFFF, distOk = r.distanceMm != 0xFFFF;
  char level[16], dist[16];
  if (levelOk) snprintf(level, sizeof(level), "%u.%u", r.levelX10 / 10, r.levelX10 % 10); else snprintf(level, sizeof(level), "null");
  if (distOk) snprintf(dist, sizeof(dist), "%u.%u", r.distanceMm / 10, r.distanceMm % 10); else snprintf(dist, sizeof(dist), "null");
#define B(m) ((r.flags & (m)) ? "true" : "false")
  return snprintf(out, cap,
    "{\"schema\":%d,\"node\":%u,\"type\":\"rain\",\"mac\":\"%s\",\"boot\":%lu,\"seq\":%lu,\"recovered\":%s,\"backlog\":%s,"
    "\"rain\":%s,\"waterLevel\":%s,\"levelValid\":%s,\"distance\":%s,\"turbidity\":%u,\"turbidityValid\":%s,"
    "\"waterClear\":%s,\"tankFull\":%s,\"valveOpen\":%s,\"valveState\":%u,\"valveReason\":%u,"
    "\"buzzer\":%s,\"firstFlush\":%s,\"dryRun\":%s,\"outOfRange\":%s,\"faults\":%u,\"rainEvents\":%u,"
    "\"uptime\":%lu,\"rxEpoch\":%lu,\"gwUptimeMs\":%lu,\"channel\":%u,\"rssi\":%d}",
    AQUA_JSON_SCHEMA, t.number, mac, (unsigned long)t.bootId, (unsigned long)t.seq,
    (t.flags & AQUA_TF_RECOVERED) ? "true" : "false", (t.flags & AQUA_TF_BACKLOG) ? "true" : "false",
    B(AQUA_RF_RAIN), level, B(AQUA_RF_TANK_VALID), dist, r.turbidityAdc, B(AQUA_RF_TURB_VALID),
    B(AQUA_RF_WATER_CLEAR), B(AQUA_RF_TANK_FULL), B(AQUA_RF_VALVE_OPEN), r.valveState, r.valveReason,
    B(AQUA_RF_BUZZER), B(AQUA_RF_FIRST_FLUSH), B(AQUA_RF_DRY_RUN), B(AQUA_RF_OUT_OF_RANGE), r.faults, r.rainEvents,
    (unsigned long)r.uptimeS, (unsigned long)t.rxEpoch, (unsigned long)t.rxUptimeMs, t.channel, (int)t.rssi);
#undef B
}

static inline int aquaJsonTelemetry(char* out, size_t cap, const AquaTelemetry& t) {
  return t.devType == AQUA_DEV_RAIN ? aquaJsonRain(out, cap, t) : aquaJsonFlow(out, cap, t);
}

struct AquaGwHealth {
  const char* gatewayId;
  uint32_t uptimeS, epoch, freeHeap;
  bool wifiUp, mqttUp;
  int rssi; uint8_t channel;
  uint32_t qDepth, qCapacity, qOverflow, published, publishFail, mqttReconnects, wifiReconnects, channelChanges;
  uint32_t rxDropped;          // ESP-NOW frames dropped because the receive queue was full
};

static inline int aquaJsonGatewayHeartbeat(char* out, size_t cap, const AquaGwHealth& g,
                                           const AquaGatewayCore& core, uint32_t nowMs) {
  int n = snprintf(out, cap,
    "{\"schema\":%d,\"gateway\":\"%s\",\"uptime\":%lu,\"epoch\":%lu,\"wifi\":{\"up\":%s,\"rssi\":%d,\"channel\":%u,"
    "\"reconnects\":%lu,\"channelChanges\":%lu},\"mqtt\":{\"up\":%s,\"reconnects\":%lu,\"published\":%lu,"
    "\"publishFail\":%lu},\"queue\":{\"depth\":%lu,\"capacity\":%lu,\"overflow\":%lu},\"freeHeap\":%lu,"
    "\"rx\":{\"frames\":%lu,\"rejected\":%lu,\"badTag\":%lu,\"badValue\":%lu,\"unknownMac\":%lu,"
    "\"acks\":%lu,\"nacks\":%lu,\"queueDropped\":%lu},\"nodes\":[",
    AQUA_JSON_SCHEMA, g.gatewayId, (unsigned long)g.uptimeS, (unsigned long)g.epoch,
    g.wifiUp ? "true" : "false", g.rssi, g.channel, (unsigned long)g.wifiReconnects, (unsigned long)g.channelChanges,
    g.mqttUp ? "true" : "false", (unsigned long)g.mqttReconnects, (unsigned long)g.published, (unsigned long)g.publishFail,
    (unsigned long)g.qDepth, (unsigned long)g.qCapacity, (unsigned long)g.qOverflow, (unsigned long)g.freeHeap,
    (unsigned long)core.totals().framesRx, (unsigned long)core.totals().rejected, (unsigned long)core.totals().badTag,
    (unsigned long)core.totals().badValue, (unsigned long)core.totals().unknownMac,
    (unsigned long)core.totals().acksTx, (unsigned long)core.totals().nacksTx, (unsigned long)g.rxDropped);
  bool first = true;
  for (uint8_t id = 1; id <= AQUA_MAX_SLOTS && n > 0 && (size_t)n < cap; id++) {
    const AquaGatewayCore::Node& nd = core.node(id);
    if (nd.state == AquaGatewayCore::NS_FREE) continue;                  // empty registry slot
    long age = nd.everSeen ? (long)((nowMs - nd.lastSeenMs) / 1000) : -1;
    char mac[18]; aquaMacFormat(nd.mac, mac);
    n += snprintf(out + n, cap - n,
      "%s{\"node\":%u,\"type\":\"%s\",\"mac\":\"%s\",\"state\":\"%s\",\"keyed\":%s,\"ageS\":%ld,\"lastSeenEpoch\":%lu,\"boot\":%lu,\"received\":%lu,"
      "\"duplicates\":%lu,\"recovered\":%lu,\"lost\":%lu,\"missing\":%lu,\"outOfOrder\":%lu,"
      "\"nacksSent\":%lu,\"sessions\":%lu,\"invalid\":%lu,\"cumAck\":%lu,\"nodeUptime\":%lu,"
      "\"nodeStatus\":%u,\"nodeRetransmits\":%u,\"nodeDropped\":%u,\"nodeRediscoveries\":%u,\"rssi\":%d}",
      first ? "" : ",", nd.number, aquaTypeName(nd.devType), mac, nd.state == AquaGatewayCore::NS_PAIRED ? "paired" : "pending",
      nd.keyed ? "true" : "false", age, (unsigned long)nd.lastSeenEpoch,
      (unsigned long)nd.rx.bootId, (unsigned long)nd.rx.received, (unsigned long)nd.rx.duplicates,
      (unsigned long)nd.rx.recovered, (unsigned long)nd.rx.lost, (unsigned long)nd.rx.missingCount(),
      (unsigned long)nd.rx.outOfOrder, (unsigned long)nd.rx.nacksSent, (unsigned long)nd.rx.sessions,
      (unsigned long)nd.invalid, (unsigned long)nd.rx.cumAck, (unsigned long)nd.nodeUptimeS,
      nd.nodeStatus, nd.nodeRetransmits, nd.nodeDropped, nd.nodeRediscoveries, (int)nd.rssi);
    first = false;
  }
  if (n > 0 && (size_t)n < cap) n += snprintf(out + n, cap - n, "],\"paired\":{\"registered\":%u,\"ok\":%lu,\"fail\":%lu,\"announces\":%lu,\"nearbyEvents\":%lu}}",
                                      core.registeredCount(), (unsigned long)core.totals().pairOk, (unsigned long)core.totals().pairFail,
                                      (unsigned long)core.totals().announces, (unsigned long)core.totals().nearbyEvents);
  return n;
}
