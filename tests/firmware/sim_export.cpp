// Integration exporter: runs the real node-link + gateway-core code in a lossy simulated radio
// environment and prints the MQTT messages the gateway WOULD publish (one per line), plus ground truth.
//   sim_export <epochBase> <seconds> <lossPercent>  >  messages.txt
// Output:  MSG <simMs> <topic> <json>      TRUTH <node> <bootHex> <pulses>
// Scenario (time in sim seconds): node1 6 L/min, node2 follows except a 2 L/min leak during [270,420);
//   gateway outage 100-125 s; hotspot channel change 6->11 at 200 s; node 1 reboots at 330 s.
#include <cstdio>
#include <cstdlib>
#include "sim_harness.h"

int main(int argc, char** argv) {
  g_epochBase = argc > 1 ? (uint32_t)strtoul(argv[1], nullptr, 10) : 1700000000u;
  uint32_t seconds = argc > 2 ? (uint32_t)atoi(argv[2]) : 600;
  double loss = (argc > 3 ? atof(argv[3]) : 20.0) / 100.0;

  Sim s; s.begin();
  s.net.loss = loss; s.net.dup = 0.10; s.net.reorder = 0.15; s.net.rng = 4242;
  s.gw.channel = 6;
  s.nodes[0].lpm = 6.0; s.nodes[1].lpm = 6.0;
  size_t printed = 0; uint32_t nextHb = 15000;
  char json[1900];

  for (uint32_t t = 0; t < seconds * 1000u; t++) {
    uint32_t sec = t / 1000;
    s.nodes[1].lpm = (sec >= 270 && sec < 420) ? 4.0 : 6.0;                  // leak between the two meters
    s.net.gwUp = !(sec >= 100 && sec < 125);                                  // gateway/hotspot outage
    if (sec == 200 && t % 1000 == 0) s.gw.channel = 11;                       // iPhone hotspot changes channel
    if (sec == 330 && t % 1000 == 0) {                                        // node 1 reboot
      printf("TRUTH 1 %08X %u\n", s.nodes[0].link.bootId(), s.nodes[0].trueTotalPulses);
      s.bootNode(0, 9001);
      s.nodes[0].lpm = 6.0;
    }
    s.step();
    for (; printed < s.gw.published.size(); printed++) {
      const AquaTelemetry& tel = s.gw.published[printed];
      aquaJsonTelemetry(json, sizeof(json), tel);
      printf("MSG %u waterleak/project001/node%u %s\n", s.net.now, tel.number, json);
    }
    if (s.net.gwUp && s.net.now >= nextHb) {
      AquaGwHealth g; memset(&g, 0, sizeof(g));
      g.gatewayId = "gw-sim"; g.uptimeS = s.net.now / 1000; g.epoch = g_epochBase + s.net.now / 1000; g.freeHeap = 150000;
      g.wifiUp = true; g.mqttUp = true; g.rssi = -60; g.channel = s.gw.channel; g.qCapacity = 256;
      aquaJsonGatewayHeartbeat(json, sizeof(json), g, s.gw.core, s.net.now);
      printf("MSG %u waterleak/project001/gateway/heartbeat %s\n", s.net.now, json);
      nextHb += 15000;
    }
  }
  printf("TRUTH 1 %08X %u\n", s.nodes[0].link.bootId(), s.nodes[0].trueTotalPulses);
  printf("TRUTH 2 %08X %u\n", s.nodes[1].link.bootId(), s.nodes[1].trueTotalPulses);
  printf("STATS node1 recovered=%u duplicates=%u lost=%u nodeRetransmits=%u | node2 recovered=%u duplicates=%u lost=%u\n",
         (unsigned)s.gw.core.node(1).rx.recovered, (unsigned)s.gw.core.node(1).rx.duplicates, (unsigned)s.gw.core.node(1).rx.lost,
         (unsigned)s.nodes[0].link.stats().retransmits, (unsigned)s.gw.core.node(2).rx.recovered,
         (unsigned)s.gw.core.node(2).rx.duplicates, (unsigned)s.gw.core.node(2).rx.lost);
  return 0;
}
