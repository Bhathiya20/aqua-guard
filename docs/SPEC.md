# Aqua Guard upgrade: gateway setup portal, QR node pairing, pipeline designer and leak analysis

You are working inside my Aqua Guard project in VS Code. Aqua Guard is an IoT water-monitoring and leak-detection system. Read this whole brief before you change anything.

## 0. How to work (mandatory)

1. **Explore first, change nothing.** Map the repository: firmware sketches, the `AquaProtocol` Arduino library, `aqua_secrets.h` (or its template), Flask backend, MQTT subscriber, SQLite schema, React + Vite + Tailwind frontend, and the `npm run dev` launcher. Then give me a short report:
   - the folder structure and what each part does;
   - how a reading travels today (node → ESP-NOW → gateway → MQTT → backend → SQLite → dashboard);
   - the MQTT topics and JSON schema that are in use;
   - every place that hard-codes node IDs, node MAC addresses or a fixed number of nodes;
   - anything in this brief that conflicts with the code.
2. **Then write a phased plan** that follows the phases in section 9: the files you will touch per phase, the protocol/schema changes and the risks. **Stop and wait for my approval** before you write code.
3. Work **one phase at a time**. At the end of each phase, give me:
   - what changed;
   - how you verified it (compiled, tests run, builds);
   - exactly what I must test on the hardware;
   - wait for my "OK" before you start the next phase.
4. **Do not break what works.** Keep these features of the existing link: cumulative pulse counter in every packet, boot ID, sequence numbers, ACK/NACK, retransmission window, automatic channel discovery, gateway MQTT queue with overflow counter, heartbeat, Last Will "offline", optional TLS and ESP-NOW encryption. Extend the protocol, don't replace it. Bump `AQUA_PROTO_VERSION` when the frame format changes, and make the backend accept both versions during the migration.
5. **Never invent hardware facts.** If you need a pin, a part value or a broker detail that is not in this brief or in the code, ask me.
6. **Verify everything you can:**
   - Compile all firmware with `arduino-cli` or PlatformIO (whichever the project uses; if neither is set up, tell me what to install). Targets: gateway `esp32:esp32:esp32`, nodes `esp32:esp32:esp32c3` with USB CDC on boot enabled, Arduino-ESP32 core 3.3.x.
   - Add host-side unit tests (plain C++ compiled with g++) for the protocol, pairing and QR-parsing logic wherever it is hardware-independent.
   - Add `pytest` tests for the backend, especially the pipeline analysis in section 7.
   - Make sure `npm run build` and `npm run dev` still work.
7. Keep secrets out of git: add a `aqua_secrets.example.h` template, and make sure `.gitignore` covers the real files.

## 1. Hardware facts (already built on breadboard)

**Gateway:** ESP32 DevKit V1 (ESP32-WROOM-32, 4 MB flash).
- Its BOOT button (GPIO0) can be used for a long-press "re-enter setup" action after boot.
- Its onboard LED (GPIO2) can show status.

**Sensor node:** ESP32-C3 SuperMini with a YF-S201 flow sensor, powered from a 2S Li-ion pack through a 5 V buck.
- Flow pulse input: **GPIO4**. It goes through a Schottky diode with a 10 kΩ pull-up to 3.3 V. Count on the FALLING edge in an `IRAM_ATTR` ISR, and protect the counter with a `portMUX` critical section.
- Battery voltage: **GPIO3** (ADC1_CH3) through a 330 kΩ / 100 kΩ divider with 100 nF. Calculate it as `Vbat = analogReadMilliVolts × (330k + 100k) / 100k` (an average of 8 samples, 11 dB attenuation).
- Onboard LED: **GPIO8**. BOOT button: **GPIO9**. Usable after boot for long-press re-pair and factory reset.
- Do not use GPIO2, GPIO8 or GPIO9 as inputs at boot (they are strapping pins).
- YF-S201 nominal calibration: 450 pulses/L (f = 7.5 × Q in L/min), accuracy about ±10 %. Calibration must be configurable per node.
- The ESP32-C3 has **no PCNT and no ULP**, so it cannot count pulses in deep sleep. Don't implement deep sleep in this upgrade; keep continuous mode, with an optional light-sleep mode only if it never loses pulses.
- Some SuperMini boards have weak antennas. Keep a configurable TX-power reduction option (`WIFI_POWER_8_5dBm`).

**Rain-water node:** ESP32, existing `Rain_water_system` sketch. Keep it working and give it the same pairing flow, but it is lower priority.

If the code contains pins or values that differ from the ones above, list the differences and ask me which is correct.

## 2. Target user experience (the complete journey)

1. The user powers up the gateway for the first time. It has no Wi-Fi saved, so it starts a Wi-Fi access point called **`AquaGuard WiFi`**.
2. The user connects with a phone or laptop. A **captive portal** opens automatically (DNS catch-all plus the usual captive-portal detection URLs for Android, iOS and Windows). If it doesn't open, the user browses to `http://192.168.4.1`.
3. **Setup step 1, network.** The page:
   - scans and lists nearby Wi-Fi networks (sorted by signal, with a manual SSID option and a password field);
   - asks for the MQTT broker details: host, port, username, password, TLS on/off (with sensible defaults);
   - makes the gateway try the connection and shows live progress: connecting → connected (IP, RSSI, channel) → MQTT connected, or a clear error with a retry option.
4. **Setup step 2, add nodes.** The page shows a simple wizard with a big **Add node** button. For each node the user either:
   - **scans the QR sticker** on the node, or
   - **types the MAC address and pairing code by hand**, when the QR cannot be scanned.
   The gateway assigns the next **node number** (Node 1, Node 2, …). The user can give it a name such as "Kitchen". The page then shows **"Waiting for Node N…"** until the node has actually joined, with instructions: "Power on the node near the gateway. Its LED blinks fast while pairing and stays solid when it has joined."
5. **Setup step 3, finish.** A summary lists the gateway, the network, MQTT status and every node with its number, name, MAC, online state and battery. The user presses **Finish**.
6. **After setup:**
   - The gateway works normally.
   - The main Aqua Guard web app (Flask + React) shows **the gateway as online** and **every node with its number**.
   - Nodes can still be **added, renamed, replaced or removed** later, either from the main web app or from the gateway's local page.
7. In the main web app the user opens the **Pipeline designer** (section 6), draws the building's pipes and places the nodes on them. The system then shows which paths are monitored for leaks, live and historical water use per branch, and leak alerts (section 7).

## 3. Gateway firmware (ESP32): provisioning, pairing, local web

### 3.1 Modes and Wi-Fi

- **Storage.** Keep all of the following in NVS (`Preferences`), not in source code:
  - Wi-Fi SSID and password;
  - MQTT settings;
  - the setup-completed flag;
  - the admin password;
  - the paired-node registry (section 3.3).
  The compile-time `AQUA_NODE_MAC_x` allowlist must go. Compile-time defaults may remain as a fallback only.
- **SETUP mode.** Runs at the first boot, and whenever BOOT is held for 5 seconds after boot:
  - starts the access point `AquaGuard WiFi` (`WIFI_AP_STA`), with a WPA2 password;
  - the default AP password is derived from the gateway MAC and printed on serial at boot, so I can put it on a label;
  - while the AP is up, ESP-NOW keeps working so nodes can pair.
- **Channel rule (this caused our past failures).** The ESP32 radio uses ONE channel:
  - When the STA connects to the router, the AP and ESP-NOW move to the router's channel.
  - The portal must survive the phone briefly dropping when that happens: the page re-polls and shows "reconnecting…".
  - Nodes keep finding the channel through the existing channel discovery.
  - Never hard-code a channel.
- **RUN mode.** After setup is finished:
  - STA only by default, as today, with `WiFi.setSleep(false)`;
  - the AP is switched off;
  - the local web page is still reachable on the gateway's LAN IP, protected by the admin password;
  - it also advertises mDNS `aquaguard.local`.
- **Wi-Fi recovery.** If the router is unreachable for more than N minutes (configurable, default 10), the gateway re-opens the setup AP in parallel so the user can fix the credentials. It keeps queueing node data meanwhile.

### 3.2 Local web server and portal

- Use **ESPAsyncWebServer + AsyncTCP**, or the built-in `WebServer` if async causes trouble with ESP-NOW.
- Use **DNSServer** for the captive portal.
- Serve gzipped static files from **LittleFS**. Add a partition scheme with room for LittleFS on 4 MB and document it.
- The portal is a **small, dependency-light, mobile-first responsive single page** in vanilla JS or Preact. Keep it under about 150 KB gzipped. It must work **with no internet**, so bundle every asset. It must also work on a desktop browser.
- JSON REST API, including at least:
  - `GET /api/status`
  - `GET /api/wifi/scan`
  - `POST /api/wifi`
  - `POST /api/mqtt`
  - `GET /api/nodes`
  - `POST /api/nodes` (add)
  - `PATCH /api/nodes/{n}`
  - `DELETE /api/nodes/{n}`
  - `POST /api/setup/finish`
  - `POST /api/factory-reset`

  Live progress is shown by polling `/api/status` every 1–2 s, or with SSE/WebSocket if that stays stable.
- **Security.**
  - The setup page can only be used while in SETUP mode, or with the admin password.
  - The admin password is set during setup.
  - Never return stored passwords in any API response.
  - Rate-limit login attempts.
- **QR scanning, an important browser limitation.** The portal is served over plain `http://192.168.4.1`, which is not a secure context. In that case browsers block live camera access (`getUserMedia`), and iOS/Android captive-portal mini-browsers block even more. Therefore implement both of these:
  1. **Photo-capture scan:** `<input type="file" accept="image/*" capture="environment">`, then decode the photo in the browser with a bundled QR decoder (for example jsQR). This works without a secure context.
  2. **QR-as-link:** the node QR contains a URL (format in 4.2). The phone's normal camera app opens it straight into the portal's add-node page with the fields pre-filled.

  Also show an "Open in browser" hint when the page detects that it is inside a captive-portal mini-browser. The main web app (section 5) may use live camera scanning, because it runs on localhost or HTTPS.
- **Manual entry:** MAC and pairing-code fields, with format validation, automatic `:` insertion, case-insensitive input, and a clear error for a duplicate MAC.

### 3.3 Node registry and pairing

- **Registry entry** (in NVS):
  - node number (1–`AQUA_MAX_SLOTS`, assigned in order, and never reused for a different MAC unless the user explicitly does a "replace");
  - MAC, name, node type (flow / rain);
  - pairing key (or its derived key);
  - calibration in pulses/L;
  - TX-power flag;
  - added-at timestamp;
  - state: pending / paired / removed.
- **Pairing handshake.** Reuse and extend the existing authenticated DISCOVERY/pairing code in `AquaProtocol`; don't write a second mechanism next to it.
  1. When a node is added, the gateway stores it as **pending**.
  2. An unpaired node broadcasts JOIN requests, authenticated (HMAC) with its own pairing key.
  3. The gateway accepts a JOIN only from a pending MAC whose HMAC verifies.
  4. The gateway replies with the assigned node number and session or link-key material, and registers the ESP-NOW peer (encrypted if `AQUA_ESPNOW_ENCRYPT`).
  5. The node stores this in its NVS, and the entry becomes **paired**.
- **Removing a node:** delete the peer, mark the entry removed, and send an UNPAIR frame if the node is reachable.
- **Replacing a node:** the new MAC takes over the old node number and history, but only after an explicit confirmation by the user.
- **MQTT sync** (the gateway is the source of truth for pairing, so it keeps working offline):
  - publish a retained `aquaguard/<gatewayId>/gateway/info` (firmware version, IP, channel, setup state);
  - publish a retained `aquaguard/<gatewayId>/nodes/registry` (number, name, MAC, type, state, last seen, battery, RSSI);
  - publish again on every change.
- **Commands from the main web app** (add / rename / remove / replace / set calibration) arrive on the existing signed command topic. Keep the HMAC signing and timestamp replay protection that is already in place. The gateway applies the command and republishes the registry.
- Use node-number-based topics for telemetry (for example `aquaguard/<gatewayId>/node/<n>`), and include the MAC in the payload. Keep the rain node's topic working.

## 4. Node firmware (ESP32-C3): one generic firmware for every node

### 4.1 No per-node compile

- One binary for all flow nodes. Remove compile-time `NODE_ID`.
- At first boot the node generates a random **pairing code** (for example 8 characters from an unambiguous alphabet) and stores it in NVS. It keeps it across reboots, and creates a new one only on factory reset.
- **States:**
  - **UNPAIRED:** LED blinks fast. Sends authenticated JOIN broadcasts and runs channel discovery. Pulse counting still works.
  - **PAIRED:** LED off (short blink on each successful send). Normal telemetry.
  - **LINK LOST:** LED blinks slowly. Keeps queueing data.
- **BOOT button (GPIO9) after boot:**
  - hold 5 s to re-enter pairing;
  - hold 15 s for a factory reset (clears the pairing code, assigned number and keys).
- **Serial output at boot:** firmware version, MAC, pairing code, the full QR payload, and an ASCII QR code (for example the `qrcode` library), so I can copy the payload or print a label.

### 4.2 QR payload format

```
http://192.168.4.1/add?v=1&mac=AA:BB:CC:DD:EE:FF&code=XXXXXXXX&type=flow
```

- Write one parser that accepts this URL form and also a compact form `AQG1;AABBCCDDEEFF;XXXXXXXX;flow`.
- Use the same parser logic in the portal JS and the main web app.
- Add unit tests for it: valid input, lower-case input, missing fields, wrong version, bad MAC.
- Add a small script, `tools/make_node_labels.py`, that turns a list of MAC/code pairs (from serial output) into printable PNG/PDF QR stickers. The sticker shows the QR plus the MAC and code in plain text, for manual entry.
- The MAC alone is not enough for pairing, because anyone can read a MAC. The pairing code is the secret.

### 4.3 Measurement and payload

- Keep the free-running cumulative pulse counter, boot ID and sequence numbers. Keep pulses/L as a runtime value received from the gateway (default 450), stored in NVS.
- **Add battery voltage (mV) to the flow payload.** This needs a protocol version bump and updates to:
  - the gateway's JSON;
  - the backend MQTT subscriber;
  - an SQLite column;
  - the dashboard.
- Report RSSI and firmware version in a periodic status frame.
- Keep the 2 s sample interval configurable.
- The backend must never lose volume: it calculates usage from the **difference in cumulative pulses within the same boot ID** and handles a counter reset when the boot ID changes.

## 5. Main web app (Flask + React + Vite + Tailwind + SQLite)

### 5.1 General

- Fully responsive (mobile, tablet, desktop). Clean and simple, with clear empty states and loading/error states.
- Use the existing auth if there is any. Otherwise add a simple login with a single admin user and hashed password; role-based access can come later.

### 5.2 Gateway and nodes

- Show the gateway card: online/offline (from the retained status plus heartbeat timeout), IP, Wi-Fi channel, RSSI, MQTT queue depth and dropped count, last seen, firmware version.
- Show the **node list** with number, name, type, state, last seen, flow now, total today, battery (V and status), RSSI and missing-packet count (from sequence gaps).
- **Add node** from the web app:
  - live camera QR scan (`BarcodeDetector` where available, otherwise a bundled library such as `html5-qrcode`), or manual entry;
  - sends the signed command to the gateway;
  - shows "waiting for node to join" until the registry reports it as paired.
- Rename, set calibration, replace (with confirmation) and remove (with confirmation).
- The web app mirrors the gateway's registry (it is not the source of truth). Show a warning if the two disagree.

## 6. Pipeline designer

Build a visual editor in which the user draws the water network and places the nodes. Use **React Flow (`@xyflow/react`)** or a similar maintained library; ask me if you want to use something else.

- **Elements in the palette:**
  - Main supply / source (exactly one per network; it can also be a tank);
  - Straight pipe;
  - **T junction** (one in, two out);
  - **L junction / elbow**;
  - Cross junction (optional);
  - Valve (optional);
  - Outlet / tap / fixture (where water is legitimately used: kitchen tap, shower, garden tap, tank inlet);
  - End cap.
- **Placing nodes.** A placed node is drawn as a **sensor on a pipe segment**, with an arrow showing the flow direction. Only paired nodes can be placed, each node at most once.
- **Pipe properties:** optional label, length and diameter (used for display only in this phase).
- **Editor behaviour:** drag, connect, rotate junctions, delete, undo/redo, zoom/pan, snap to grid, plus a mobile fallback (tap to select and tap to place).
- **Validation, with clear inline messages:**
  - the network must be one connected tree flowing away from the source; report loops or reverse flow;
  - every junction port must be connected or capped;
  - outlets must be leaves;
  - each node may be placed once;
  - warn about unconnected parts.
- Save networks as versioned graph JSON in SQLite. Allow several sites/networks if it's cheap, otherwise one.
- Calculations always use the configuration that was active when the data was recorded. Store the version ID with alerts.
- Add export/import of the JSON.

## 7. Analysis: monitored paths, usage and leaks

Implement this in the backend as pure, well-tested Python (no Flask inside the logic), and expose it through API endpoints.

### 7.1 Zones

- **Partition the network into zones.** A zone is the set of pipe segments and outlets downstream of a sensor (or of the source) and upstream of the next sensors.
  - Each zone has exactly one inflow meter (or none, if it is upstream of the first sensor) and zero or more outflow meters.
  - Highlight the zones in the designer with distinct colours, and show the **mass-balance equation** for each one: `Q_unaccounted(zone) = Q_in − Σ Q_out`.
- **Classify each zone.**
  - **Leak-monitored, strong:** the zone has an inflow meter and **no outlets**. Any sustained unaccounted flow there is a leak or a meter fault.
  - **Leak-monitored, weak:** the zone has an inflow meter and also outlets. Unaccounted flow is real usage plus possible leaks. Detect leaks there with **minimum night flow** (for example a 02:00–04:00 window, configurable) and **continuous-flow** rules (flow that never drops to zero for X hours).
  - **Not monitored:** no inflow meter. Explain the reason and **suggest where to add a node** to cover it (for example "place a node on the pipe after T-junction 2 to monitor the bathroom branch").
  - Show a **coverage summary**: the fraction of pipe and the outlets that are covered.

### 7.2 Calculations

- **Time alignment.** Never compare instantaneous readings from different nodes. Convert each node's cumulative pulses into volume per aligned time bucket (default 1 min, with 5 min / hourly / daily roll-ups), using pulse differences within each boot ID.
  - Mark buckets with missing packets or offline nodes as **incomplete**, and exclude them from leak decisions instead of treating missing data as zero.
- **Leak decision.**
  - Raise an alert when `Q_unaccounted` stays above a threshold for a minimum duration.
  - Default threshold: `max(absolute minimum L/min, combined meter tolerance %)`. With a YF-S201 at about ±10 %, use the sum of the meters' tolerances on the flows involved.
  - Make the threshold and duration configurable per zone.
  - Alert severities: suspected → confirmed (longer duration).
- **Other alerts:**
  - abnormal usage (a branch above its usual hourly profile, or continuous flow at an outlet branch);
  - node offline;
  - low battery (configurable voltage, with a 2S default around 6.6 V);
  - missing packets;
  - gateway offline;
  - meter-fault hint (downstream sum consistently larger than upstream).
- **Usage reporting:** live flow and hourly / daily / weekly / monthly volume per node, per branch (zone) and in total, plus leak event history.
- **Pytest coverage:** zone partitioning for a straight line, a tree with T junctions, an L elbow chain, an unmonitored branch, a strong zone vs a weak zone, missing buckets, a boot-ID counter reset, and threshold/duration logic.

### 7.3 Dashboard views

- Overview: total flow now, today's usage, gateway status, nodes online/offline, active alerts, battery summary.
- Pipeline view: the saved diagram with live flow on each node, each zone coloured by status (normal / suspected leak / confirmed leak / not monitored / incomplete data), and the unaccounted flow per zone.
- Analytics: charts per node and per zone over time.
- Alerts list with acknowledge/resolve.

## 8. Non-functional requirements

- **Must work offline at the gateway level:** pairing, the setup page and data buffering don't depend on the internet.
- **Robustness:**
  - the gateway reconnects to Wi-Fi and MQTT with backoff;
  - the node retransmits from its queue;
  - the ESP-NOW receive callback only copies data into a queue (no heavy work in the Wi-Fi task);
  - add a counter for dropped receive-queue items and include it in the heartbeat.
- **Security:**
  - pairing code plus HMAC for JOIN;
  - signed MQTT commands;
  - no plaintext secrets in API responses or logs (mask passwords);
  - the public test broker is allowed only for development, behind a clear flag.
- **Code quality:** small modules, typed Python, consistent naming, and comments that explain *why*. Update the README:
  - wiring;
  - flashing steps;
  - the setup walkthrough with screenshots or descriptions;
  - the MQTT topic list;
  - the QR format;
  - the API list.

## 9. Phases (propose adjustments in your plan if needed)

1. **Repository survey and plan.** Report and plan only; no code.
2. **Protocol and node firmware:**
   - generic node firmware, NVS identity, pairing code, QR output, JOIN/UNPAIR;
   - battery field, protocol version bump;
   - host tests;
   - compile for the C3.
3. **Gateway firmware:**
   - NVS config, SETUP/RUN modes, AP + captive portal, Wi-Fi/MQTT provisioning, node registry, pairing, REST API, MQTT registry sync;
   - LittleFS portal UI with photo-QR scan, QR-link and manual entry;
   - compile for the ESP32.
4. **Backend:**
   - MQTT subscriber for the new topics and payloads (backward compatible);
   - DB migrations (gateways, nodes, readings with boot ID and cumulative pulses, networks, network versions, zones, alerts);
   - node-management endpoints that send signed commands.
5. **Frontend:** gateway and node management, add-node with QR scan, responsive layout.
6. **Pipeline designer:** editor, validation, save/version.
7. **Analysis engine:** zones, coverage, time-bucketed balance, leak and usage alerts, pytest suite, dashboard and pipeline-view integration.
8. **End-to-end checklist** for me to run on the breadboard:
   - first boot → portal → Wi-Fi + MQTT → add 2 nodes by QR and 1 manually → finish;
   - dashboard shows the gateway online and the nodes numbered;
   - draw a network with a T junction and place the nodes;
   - simulate a leak by running water from a point between two nodes, and check the zone alert.

Start with phase 1 now.


---
## Implementation notes (2026-11-16)
- Gateway flashing: only `scripts/flash_gateway.mjs` (app at 0x10000, NVS untouched); the plain upload path overwrites the last NVS page. A partition fix is a TODO after the water test.
- `aquaguard.local` is unreliable on Windows; use the IP.
- Leak detection uses 5 s windows (two consecutive needed). Leak states: NORMAL, SUSPICIOUS, POSSIBLE_LEAK, MEASUREMENT_INCONSISTENCY.
- Usage states: NO_FLOW, NORMAL, PROLONGED_USAGE (warning), CONTINUOUS_ABNORMAL_USAGE (critical), NIGHT_FLOW (critical, 60 s confirm). Missing data = unknown. Independent of the balance detection.
- Decisions: default profile 15 min / 30 min; manual resolve allowed for leak alerts; usage endpoint is `/usage-profile`.
