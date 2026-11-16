# Node 84:C7:BB:FC:5D:00

| Field | Value |
|---|---|
| MAC | `84:C7:BB:FC:5D:00` |
| Board | ESP32-C3 SuperMini, YF-S201 on GPIO4 (1 k pull-up to 3.3 V), sensor on the 5 V buck |
| COM port used for flashing | COM34 (the port can change; the MAC is the identity) |
| Firmware | `firmware/flow_node_c3` (shared by all nodes), repo commit `10e7d16` (battery-none + TX power support), reflashed 2026-11-16 on COM34 with watchdog reset |
| First boot after flashing | reset reason: POWERON (power applied / regulator) (uptime 8 s); node state: JOINING (before pairing); paired afterwards |
| hw_test_c3 result | PASS 2026-11-15: boots, GPIO4 idles HIGH (2922 mV), 456 pulses counted (up to 26 Hz) with raw edges = pulses, HIGH/LOW swing seen, BOOT PRESSED/RELEASED seen |
| LED polarity | active-HIGH (confirmed by the owner on the bench) |
| Node number / name | **Node 4, "right sub"** on gateway `gw-C41028` (since the 2026-11-16 registry re-add; was Node 3) |
| Installation location | |
| Calibration | 450 pulses/L (nominal, not measured) |
| Battery divider | 220 k / 100 k as designed; **not fitted on this bench board, so the battery reading is meaningless here** |

Files: `boot.txt` (banner, code masked), `sticker.png` / `sticker.pdf` and `secret.txt` hold the pairing code and are **git-ignored**.
