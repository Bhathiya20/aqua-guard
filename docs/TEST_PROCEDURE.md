# Full system test procedure

## A. Automated (no hardware)
1. Firmware logic: `clang++ -std=c++17 -Ilibraries/AquaProtocol/src -Itests/firmware tests/firmware/test_aqua.cpp -o test_aqua && ./test_aqua`
2. Backend: `cd Aqua-Guard-WCS-UI/backend && .venv/Scripts/python -m pytest -q`
3. Compile all four sketches (commands in the root README).
4. Dashboard: `npm run build` in `Aqua-Guard-WCS-UI`.
5. Live API smoke test: backend with `AQUAGUARD_MQTT_ENABLED=false` and a scratch `AQUAGUARD_DB_PATH`, run `simulate_sensors.py --direct --leak --rain`, log in, open every page.

## B. Hardware (must be run by you — not done yet)
Prepare: the same `aqua_secrets.h` for all builds; serial monitors at 115200; backend + dashboard running; MQTT broker reachable.

1. **Gateway**: serial shows Wi-Fi connect, channel, MQTT connect. Devices page: gateway ONLINE, heartbeat age < 20 s, channel equals the hotspot's.
2. **Discovery**: power Node 1, Node 2, rainwater node in any order. Each logs scan → discovery → "locked on channel N" within ~15 s; all *online*.
3. **Channel follow**: change the hotspot channel (toggle hotspot / change AP). Expect ACK loss → backoff → re-scan → recovery with no manual action; `Channel changes` increments. Record the recovery time.
4. **Gateway outage**: hotspot off for 60 s. Backend: gateway OFFLINE after ~45 s, nodes *unknown* (not offline), alert raised. Restore: backlog flushes, alert clears, no duplicate rows.
5. **Node outage**: unplug Node 2 only → *offline* (gateway online) + alert. Replug → new session counted, volume continues.
6. **Flow accuracy**: pour a measured volume (e.g. 5 L) through each meter; compare dashboard litres; adjust `PULSES_PER_LITER` per node; repeat 3×.
7. **Loss recovery**: create ≥ 10 % loss on a node (distance/obstruction). Devices page shows recovered/duplicates; total litres still equal the poured volume.
8. **Leak detection**: meters in series, steady flow > 2 L/min → NORMAL. Open a drain between them > 60 s → SUSPECTED → CONFIRMED + alert; close → RESOLVED. Unplug one node during flow → SENSOR_FAULT, not a leak. Tune `AQUAGUARD_LEAK_*` from observed noise.
9. **Rainwater (dry-run)**: wet the module → states advance, "dry-run" banner, relay stays off (verify with a meter). Block/unplug ultrasonic → "sensor invalid", never 0 %. Dirty water → turbid, collection stopped. Near-full tank → tank-full alert.
10. **Valve**: only after the checklist in `WIRING_AND_SAFETY.md`.
11. **Dashboard**: login, forced password change, logout/login, reload keeps session, a normal user cannot reach Admin (URL and API both refused), phone on the LAN reaches the UI, stop the backend → "connection lost" banner → restart → recovers by itself.
12. **Soak**: 24 h; check gateway `freeHeap`, reconnect counters, queue overflow = 0, total litres vs. an independent meter.

---

## Phase 3 hardware checklist (gateway firmware) — PASSED on hardware, 2026-11-15

Firmware commit `7b436d5` (gateway on COM29 = ESP32 DevKit V1 + 1.3" SH1106 OLED, flow node on COM35 = ESP32-C3 SuperMini at low TX power), iPhone (Safari) on a home 2.4 GHz Wi-Fi, public broker `broker.emqx.io:1883` (no TLS; remote add correctly refused).
Verified by the user: OLED RUN screen (`http://<ip>`, MQTT up, Nodes 1/1 online); portal in Safari at the gateway IP incl. login and address card; Remove -> node fast-blinks and shows under Nodes nearby; camera photo fills MAC + code; Add -> "Node 1 joined"; wrong code -> "Pairing failed", never joined; gateway reboot and node reboot -> node rejoins by itself; range OK at the distances tried; water/blow test after joining: `cum=` rises, readings normal; web app (`npm run dev`) shows live data from Node 1.
Findings: (1) the SuperMini must run at 8.5 dBm (`per-node TX power (Nodes page)`, default 1): at full power no frame reached the gateway. (2) `window.confirm()` is suppressed by some captive-portal mini browsers, which made "Remove" do nothing there; the portal now uses in-page confirmations (regression test `scripts/test_portal_ui.mjs`).
Not covered yet: TLS broker path and remote add from the web app (Phase 4/5), items 9 (recovery AP after a long router outage) and 11 (factory reset) were not reported - re-run them before the soak test.


Prerequisites: `python tools/generate_secrets.py` was run **before** flashing every device (same keys on gateway and nodes), a flow node is flashed and shows its code on serial.

1. Flash the gateway (`arduino-cli ... upload`) and the portal (`node scripts/build_portal.mjs --flash COMx`, serial monitor closed).
2. Gateway serial (115200): banner with `gw-XXXXXX`, MAC, `Setup AP: "AquaGuard WiFi"  password: XXXXXXXXXX`. **Report** whether the GPIO2 LED blinks at about 1 Hz (setup) and whether it is active-HIGH.
2b. OLED (SDA 21, SCL 22): serial prints `[OLED] display at 0x3C (SH1106)`. SETUP screen shows the AP name, the password and `http://192.168.4.1`, and every 4 s a QR: scan it with a phone/laptop camera - it must offer to join `AquaGuard WiFi`. If the picture is shifted/garbled build with `-DOLED_DRIVER_SSD1306`. In RUN mode (after Finish) the password must NOT appear anywhere on the screen; events (`Node 1 joined`, `Nearby: 4B:88`, `Wi-Fi lost`, `MQTT lost`) show for about 5 s and never a pairing code. Unplug the OLED and reboot: the gateway must still work (one `[OLED] no display found` line).
3. Phone: join "AquaGuard WiFi" with that password. Does the portal open by itself (captive portal)? If not, does `http://192.168.4.1` work? Report phone model / OS.
4. Create the gateway password, scan Wi-Fi (your router appears), connect. The status shows connecting -> connected with IP, RSSI and **channel**. The phone may disconnect for a moment (channel move): does the page recover by itself?
5. MQTT: enter your broker (try plain first, then TLS with a real certificate). A wrong password must show a clear error; the right one "connected".
6. Add node 1 by **photo of the sticker/serial QR**, then power the flow node near the gateway: "Waiting for Node 1..." -> "has joined"; node LED goes solid for 10 s, node serial shows `PAIRED`.
7. Add a second node **by typing MAC + code** (upper/lower case, with and without dashes), and one by picking it from **Nodes nearby** and typing only the code. Try adding the same MAC twice (clear error) and a wrong code (it never joins; the portal keeps "waiting").
8. Finish. The AP closes; `http://aquaguard.local` (or the IP) asks for the password. Reboot the gateway: it comes back in RUN mode, nodes rejoin by themselves, `<prefix>/<gw>/nodes/registry` is on the broker (retained), telemetry on `<prefix>/<gw>/node/<N>` and `<prefix>/node<N>`.
9. Hold BOOT 5 s: setup AP re-opens. Power the router off for 10+ min (or set a shorter recovery time): the AP re-opens by itself and node data is queued, then delivered after reconnection.
10. Remove a node in the portal: the node restarts unpaired (LED fast blink) and shows up under "Nodes nearby". Replace a node (new hardware) and check it keeps its number.
11. Factory reset (portal or by erasing flash) returns to setup with an empty registry.

## Phase 2 hardware checklist (ESP32-C3 node, protocol v2) — PASSED on COM35 (items 2-7; battery check and water calibration still open)

Host-only so far (`test_aqua`, `test_join`, pytest, tools tests, and real compiles for ESP32-C3 + ESP32). The gateway firmware that
accepts these nodes is Phase 3, so this checklist can only verify the node on its own:

1. `python tools/generate_secrets.py`, `node scripts/compile_firmware.mjs flow_node_c3`, flash the SuperMini (USB CDC on boot).
2. Serial (115200): banner shows firmware, MAC, a 12-symbol code (`XXXX-XXXX-XXXX`), the URL payload and an ASCII QR; a phone camera reads the QR. Reboot: **same code**.
3. LED: blinks fast while unpaired (polarity already verified: active-HIGH).
4. Pulses: blow/pour through the YF-S201 or short GPIO4 to GND repeatedly - `cum=` rises, never drops; check ~450 pulses per litre against a measured volume.
5. Battery: serial `batt=` against a multimeter on the pack (2S, ~6.0-8.4 V). Report the error; the divider constant is `VBAT_DIV_R_*`.
6. BOOT button: 5 s -> restart (keeps code); 15 s -> factory reset and restart, **same code** (compare with the sticker). Serial `regen-code` then `regen-code confirm` -> **new** code + QR + sticker warning; anything else cancels.
6b. Rain node (ESP32 DevKit): same checks with BOOT (GPIO0) and the onboard LED (GPIO2); report the LED polarity.
7. Keep the node powered 10 min with no gateway: it must keep counting and not crash (channel scan back-off).

## Milestone 3 (RETIRED design) hardware checklist — never run, superseded

Prerequisites: ESP‑01S + YF‑S201 wired per `docs/WIRING_AND_SAFETY.md` (3.3 V signal), ESP32 gateway, phone hotspot or router, a **private** MQTT broker if possible, Node_01/Node_02 still deployed. Use the dry‑run rainwater valve as before (do not energise a real valve for these tests).

Setup
1. `tools/provision_device.py --init-keys`, then flash **resevier** (gateway). Serial shows `[MQTT] connected` and later `[WiFi] … channel N`; confirm NTP time (heartbeat `epoch` ≠ 0 in the UI/DB).
2. `tools/provision_device.py --name "Test sensor"` → note the code; flash **Flow_Node** to the ESP‑01S (hold GPIO0 low only for flashing). Serial: `PAIRABLE FLOW SENSOR uid=…` then `UNPAIRED`.
3. Start `npm run dev`, sign in as admin.

Pairing
| # | Step | Expected | Result |
|---|---|---|---|
| H1 | Devices ▸ Add device with the sensor ON | it appears within ~30 s with correct MAC, fw 1.0, channel = hotspot channel | |
| H2 | enter a WRONG code 5× | 403 each, 5th locks; no `auth` appears on the gateway serial | |
| H3 | enter the right code (after lock expiry or new code) | gateway serial `[PAIR] authorize … ok`; sensor `[PAIR] paired, slot 4`; UI shows paired/online | |
| H4 | blow into / run water through the sensor | live L/min and volume rise; `flow_samples.node_id` = uid | |
| H5 | compare volume with a measuring jug (1 L) | within sensor tolerance; adjust calibration in UI and re‑test | |
| H6 | reboot the sensor | re‑joins without the code; new boot id; counting continues | |
| H7 | reboot the gateway | sensor re‑announces; backend re‑authorises automatically; data resumes | |
| H8 | change hotspot channel / toggle hotspot | sensor re‑discovers channel and resumes; no lost volume (cumulative counter) | |
| H9 | power the sensor OFF | UI shows offline after ≈30 s (gateway online) | |
| H10 | Unpair in UI | gateway `[PAIR] revoke … ok`; sensor returns to UNPAIRED; its data is no longer stored | |
| H11 | try to pair a device flashed with a wrong/other secret | gateway reports failure, never paired | |
| H12 | with Node_01/Node_02 running throughout | their data, ACKs and totals unaffected (compare with before) | |
| H13 | run 30 min with 3 nodes + sensor | no growth of rejected/lost counters beyond RF losses | |

Record: gateway serial log, `pairing_attempts` rows, free heap on the gateway heartbeat. Report failures before any further firmware work.

## Troubleshooting: node stays on `SCAN` (captured with `tools/serial_capture.py`)
Capture both devices at once (close any serial monitor first; use the backend venv python, which has pyserial):
`Aqua-Guard-WCS-UI/backend/.venv/Scripts/python.exe tools/serial_capture.py gateway=COM29 node=COM35 --seconds 60 --out logs/pairing.txt`
Lines to read, in handshake order:
1. Node `[LINK] scanRounds=.. sendFail=.. rejectedRx=..`: sendFail>0 = radio not sending; rejectedRx>0 = frames arrive but fail the PSK check (different `aqua_secrets.h`).
2. Gateway `[RX] ch=N frames=.. raw=.. discovery=.. announces=..` every 5 s: `raw=0` = nothing reaches the gateway radio (distance/antenna/TX power); `badTag>0` = secrets mismatch; `discovery>0` but the node stays SCAN = the reply is not heard; `announces>0` = the gateway sees the node (it appears under Nodes nearby).
3. Node `up=.. PAIRED ch=N`: handshake done. Gateway `[PAIR] Flow N joined`.
Finding on this hardware: with full TX power `raw=0` forever; the SuperMini builds now default to 8.5 dBm (`per-node TX power (Nodes page)`, set 0 to disable) and pair within seconds. Keep node and gateway about 1-2 m apart for the first test.
