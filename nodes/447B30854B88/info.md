# Node 44:7B:30:85:4B:88  (the first node) - RETIRED

**Status: retired - damaged C3, replaced on 2026-11-16 by 44:BD:8D:5D:EB:88** (see `nodes/44BD8D5DEB88/`). Do not reuse this board's record; its history stays on the gateway under Node 2.

| Field | Value |
|---|---|
| MAC | `44:7B:30:85:4B:88` |
| Board | ESP32-C3 SuperMini, YF-S201 on GPIO4 (1 k pull-up to 3.3 V), sensor on the 5 V buck |
| COM port used for flashing | COM35 (not connected during the 2026-11-15 bench session) |
| Firmware | `firmware/flow_node_c3` (shared), flashed during Phase 3 bring-up (repo commit `7b436d5`/`584d4c5` era, low TX power default); not re-flashed on 2026-11-15 |
| hw_test result | verified during bring-up (circuit as built, LED active-HIGH); not re-run with the new bench test |
| LED polarity | active-HIGH |
| Node number / name | **Node 2, "Main outlet"** on gateway `gw-C41028` (paired 2026-11-15) |
| Installation location | |
| Calibration | 450 pulses/L (nominal, not measured) |
| Battery divider | 220 k / 100 k (battery check still open) |

Note: the gateway registry also holds a stale **pending** "Node 1" (MAC `44:7B:39:85:4B:88`, one character different), probably a typo from an earlier add. It is not a real board.

Files: `sticker.png` (copied from `labels/`; **git-ignored**, contains the pairing code). `secret.txt` is not created for this node: the code is on the sticker only, and no banner was captured in this session. `boot.txt` was not captured.
