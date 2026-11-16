# Node 44:BD:8D:5D:EB:88  (replacement for the old Node 2 board)

| Field | Value |
|---|---|
| MAC | `44:BD:8D:5D:EB:88` |
| Board | ESP32-C3 (QFN32, rev v0.4, 4 MB flash), YF-S201 on GPIO4 (1 k pull-up to 3.3 V) |
| COM port used for flashing | COM40 (USB only; 3.3 V regulator disconnected while on USB) |
| Firmware | `firmware/flow_node_c3` (shared), built from repo commit `7dffe0a`, flashed 2026-11-16, watchdog reset after flashing; same `aqua_secrets.h` as all other boards (not regenerated) |
| hw_test result | 2026-11-16 `firmware/hw_test_c3`: boots (MAC read), LED active-HIGH blinks (confirmed by eye), BOOT button PRESSED/RELEASED seen, GPIO4 idles HIGH (ADC 2891 mV), blowing through the sensor gave pulses and a HIGH/LOW swing (counter 0 -> 480) |
| LED polarity | active-HIGH |
| Node number / name | **Node 1, "main"** on gateway `gw-C41028` (since the 2026-11-16 registry re-add; Replace of the old Node 2 was not done, so it is Node 1) |
| Installation location | |
| Calibration | 450 pulses/L (nominal, carried over from the old record) |
| Battery | none (No battery build) |

Files: `boot.txt` (pairing code masked, tracked). `secret.txt` and `sticker.png` contain the pairing code and are git-ignored.
