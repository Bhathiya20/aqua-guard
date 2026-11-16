# Wiring and electrical safety notes

> Engineering notes derived from the firmware pin map and the parts named in the project. **Check them against your actual boards and datasheets; nothing here has been verified on physical hardware.** Mains-powered valves/pumps: have a competent person wire and inspect them.

## Gateway (ESP32 DevKit V1), `firmware/gateway`
Onboard BOOT button (GPIO0): hold 5 s after boot to re-open the setup AP. Onboard LED (GPIO2, assumed active-HIGH, unverified): 1 Hz = setup, fast = Wi-Fi down, slow = MQTT down, solid = all up. GPIO0 and GPIO2 are strapping pins: they are only used after boot. Power from USB or a clean 5 V supply (Wi-Fi + ESP-NOW peaks around 250 mA).

**Status OLED (optional), 1.3" 128x64 I2C:**

| OLED pin | DevKit V1 |
|---|---|
| VCC | 3V3 |
| GND | GND |
| SDA | GPIO21 |
| SCL | GPIO22 |

I2C address 0x3C (0x3D is tried too). 1.3" modules are normally **SH1106** (the default driver, library U8g2 2.36.19, pinned in `scripts/setup_firmware_toolchain.mjs`); for an SSD1306 module build with `-DOLED_DRIVER_SSD1306`. If no display answers at boot the gateway logs `[OLED] no display found` once and runs normally; the display is updated from the main loop only, at most twice a second, and never blocks ESP-NOW (frames wait in the receive queue meanwhile).
* **SETUP mode:** `AquaGuard WiFi`, the AP password, `http://192.168.4.1`, and every 4 s a QR code that joins the setup Wi-Fi (`WIFI:T:WPA;S:AquaGuard WiFi;P:<password>;;`).
* **RUN mode:** Wi-Fi status + channel + RSSI, IP, MQTT up/down, time synced, nodes online/total, queue depth. The AP password is never shown in RUN mode (not even while the recovery AP is open), and pairing codes are never shown anywhere (events show only the last two MAC bytes).
* **Events (about 5 s, top bar):** `Node N joined`, `Rain N joined`, `Pairing failed`, `Nearby: AA:BB`, `Wi-Fi lost`, `MQTT lost`.
The OLED is not part of the Phase 3 safety/pairing logic: a wiring mistake can only blank the display (check 3V3, not 5 V, unless your module has a regulator).

## Flow node (ESP32-C3 SuperMini + YF-S201), `firmware/flow_node_c3` - soldered board, as built and tested
| Signal | GPIO | Notes |
|---|---|---|
| YF-S201 pulse | 4 | sensor yellow wire **directly** to GPIO4, with a **1 kΩ pull-up from GPIO4 to the C3 3.3 V pin**. No series resistor, no 100 nF, no divider, no diode. Counted on the FALLING edge in an `IRAM_ATTR` ISR with a 1000 µs glitch filter, counter guarded by a `portMUX`. |
| Battery sense | 3 (ADC1_CH3) | **currently 220 kΩ / 100 kΩ** divider + 100 nF (to become 330 kΩ / 100 kΩ later: change `VBAT_DIV_R_TOP` in the sketch at the same time); `Vbat = analogReadMilliVolts x (R_TOP+R_BOT)/R_BOT`, average of 8 samples, 11 dB attenuation. |
| Onboard LED | 8 | strapping pin: output only. Polarity **verified: active-HIGH** (`LED_ACTIVE_LOW 0`). |
| BOOT button | 9 | strapping pin: used as an input only after boot (hold 5 s = re-pair, 15 s = factory reset). |

**Power and grounds (as built):** the YF-S201 is powered from the 5 V buck converter; its GND is joined to the C3 GND (common ground is required). The C3 is powered from a separate 3.3 V regulator into the 3.3 V pin; **do not connect USB at the same time** as that regulator.

**Why 1 kΩ:** the pull-up value was chosen so that the GPIO4 HIGH level stays at or below 3.45 V (the C3 input must not be driven well above its 3.3 V supply by a sensor powered at 5 V). Do not replace it with a larger pull-up without re-measuring GPIO4 HIGH with the sensor running. Measured GPIO4 HIGH / LOW voltages: not recorded yet (to be added). Signal check: 66-137 Hz at 8.8-18 L/min, clean waveform.

Do not use GPIO2, GPIO8 or GPIO9 as inputs at boot. The C3 has no PCNT and no ULP, so it cannot count pulses in deep sleep: the node runs continuously. Weak SuperMini antennas: the gateway can push a "reduce TX power" flag (8.5 dBm). Calibration defaults to 450 pulses/L (YF-S201 nominal, about ±10 %) and is configurable per node.

## RETIRED: flow nodes (ESP-01S + YF-S201), `firmware/legacy/Node_01` / `Node_02`
| Signal | ESP-01S pin | Notes |
|---|---|---|
| Pulse in | GPIO2 | Interrupt-counted. **GPIO2 must be HIGH at boot** (boot-mode strap). The ESP-01S blue LED is also on GPIO2 (active-low). |
| Power | 3V3 / GND | Supply must handle ≥ 300 mA peaks (Wi-Fi TX). A weak supply causes resets → new boot ids, and pulses counted between the last packet and the reset are lost. |

* The YF-S201 is a **5 V** Hall sensor. **Do not feed 5 V into an ESP8266 pin.** Power the sensor from 3.3 V (check it still pulses over your flow range) or use a level shifter / divider (e.g. 10 kΩ + 20 kΩ), plus a 10 kΩ pull-up to **3.3 V** on GPIO2 so the strap level is HIGH at boot.
* Calibration: default 450 pulses/L (`PULSES_PER_LITER` in each sketch). Real meters vary ±10 % — calibrate each node against a measured volume. Leak thresholds depend on this.

## Rainwater node (ESP32), `firmware/rain_node`
| Function | GPIO | Notes |
|---|---|---|
| Rain module digital out | 27 | LOW = wet |
| HC-SR04 TRIG | 5 | |
| HC-SR04 ECHO | 18 | ECHO is 5 V: **use a divider (e.g. 1 kΩ/2 kΩ) or level shifter** |
| Turbidity analog | 35 | input-only ADC1; module output must stay ≤ 3.3 V |
| Relay IN | 26 | polarity set by `RELAY_ACTIVE_LOW` |
| Buzzer | 15 | assumed **active** buzzer; GPIO15 is a strap pin (must not be pulled HIGH at boot — use a transistor/series resistor if needed) |

`TANK_HEIGHT_CM = 30` is the previously assumed value: measure sensor-face-to-bottom and edit it. The HC-SR04 has a blind zone (~2 cm) and is unreliable with condensation/foam, so bad echoes are reported as *invalid*, never as 0 %.

## Valve and relay — safety
1. Firmware ships in **dry-run** (`AQUA_VALVE_OUTPUT_ENABLE 0`): the relay pin is driven to the de-energised level before it becomes an output, at boot and every cycle; the dashboard shows a dry-run banner. Enable the output only after the checklist below.
2. Prefer a normally-closed valve (power to open → `VALVE_ENERGIZE_TO_OPEN 1`): power or ESP failure then closes it. `VALVE_FAILSAFE_OPEN 0` commands *closed* on sensor faults; use 1 only if the plumbing has an independent overflow path.
3. Relay modules may glitch at power-up: confirm with a meter/LED that the load stays off while the ESP32 boots and resets.
4. Valve wiring: keep the load side physically separate; fuse it; flyback diode (DC valves) or snubber (AC); never power an inductive load from the ESP supply; water-rated enclosure and strain relief.
5. There is **no valve position feedback**. "Valve open" in the UI means *commanded* open.

### Bench checklist before enabling the real valve
- [ ] Wiring checked with the load supply **disconnected**; relay LED/continuity verified for each commanded state.
- [ ] Dry-run build: wet the rain module; serial log shows CLOSED → debounce → first flush → open-collecting → closed.
- [ ] Cover/unplug the ultrasonic sensor: FAULT_SAFE, valve commanded closed (or the configured fail-safe).
- [ ] Unplug the turbidity sensor / use dirty water: collection stops.
- [ ] Reset and power-cycle repeatedly: relay never pulses at boot.
- [ ] Only then connect the load supply, rebuild with `-DAQUA_VALVE_OUTPUT_ENABLE=1`, and test at low pressure while supervised.
