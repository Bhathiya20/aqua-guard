# Rain node 30:76:F5:F7:C0:38

| Field | Value |
|---|---|
| MAC | `30:76:F5:F7:C0:38` (ESP32-D0WD-V3; the gateway's MAC ends C4:10:28, this is not it) |
| Board | ESP32 DevKit: rain GPIO27 (LOW = wet), HC-SR04 TRIG 5 / ECHO 18 (1k/2k divider), turbidity GPIO35 (higher = clearer), relay GPIO26 active-LOW, LEDs 14 green / 12 yellow / 13 red, buzzer 15, status LED GPIO2 (active-HIGH), BOOT 0, tank 30 cm |
| COM port used for flashing | COM29 (the port can change; the MAC is the identity, always read it first) |
| Firmware | `firmware/rain_node`, repo commit `10e7d16` (out-of-range flag), reflashed 2026-11-16 on COM39, **DRY-RUN** (`AQUA_VALVE_OUTPUT_ENABLE 0`: the relay is never energised) |
| Wiring confirmed by the owner | ECHO through a 1k/2k divider; turbidity output <= 3.3 V; yellow LED GPIO12 -> resistor -> LED -> GND; buzzer GPIO15 to GND without pull-up |
| `rain_hwtest` result 2026-11-15 | Serial log: relay pin GPIO26 stayed HIGH (de-energised); rain pin dry -> WET -> dry; ultrasonic about 131 cm with nothing in front; turbidity clear 3630-3650 (below the 4000 safety limit); GPIO2 toggles. Verified **by the owner by eye** (not in the log): green, yellow (lit while wet) and red LEDs, buzzer beep, onboard LED blinks active-HIGH, ultrasonic 10 cm / 20 cm with a flat book, muddy water reads clearly lower than clear, BOOT button works |
| First boot after flashing | POWERON, state SCANNING on channel 9 before pairing |
| Node number / name | assigned when added on the gateway page (type **rain**) |
| Installation location | |
| Valve | DRY-RUN until the owner bench-validates the relay and valve interlock |

Files: `boot.txt` (banner, code masked), `sticker.png` / `sticker.pdf` and `secret.txt` hold the pairing code and are **git-ignored**.
