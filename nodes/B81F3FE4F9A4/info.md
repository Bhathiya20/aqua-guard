# Node B8:1F:3F:E4:F9:A4  (replacement for 44:BD:8D:5D:EB:88, Node 1 "main")

| Field | Value |
|---|---|
| MAC | `B8:1F:3F:E4:F9:A4` |
| Board | ESP32-C3 (QFN32, rev v0.4, 4 MB flash), YF-S201 on GPIO4 (1 k pull-up to 3.3 V). Circuit unchanged from the old Node 1 board; only the C3 was exchanged. |
| COM port used for flashing | COM41 (native USB; the MAC is the identity) |
| Firmware | `firmware/flow_node_c3` (shared), battery-none build, flashed 2026-11-17. The board was **blank** (no bootloader / partition table), so besides `scripts/flash_node.mjs` (app at 0x10000) the full image `flow_node_c3.ino.merged.bin` was written at 0x0, then a watchdog reset. Same `aqua_secrets.h` as every other board (not regenerated). |
| First boot | Boots and scans (`JOINING ch=1 ... batt=none`). Pairing needs a running gateway; not paired at the time of writing. |
| hw_test | Not run on purpose (owner: circuit unchanged, no pulse / LED / battery tests needed). |
| TX power | default 8.5 dBm |
| Node number / name | **Node 1, "main"** once added as the first flow node on the gateway (the backend keys on type + number, so Node 1 keeps its history and pipeline place; the MAC behind it changes like a hardware Replace). |
| Installation location | |
| Calibration | 450 pulses/L (nominal, carried over) |
| Battery | none (No battery build; the floating ADC pin shows meaningless values) |

Files: `boot.txt` (pairing code masked, tracked). `secret.txt` and `sticker.png` contain the pairing code and are git-ignored.
