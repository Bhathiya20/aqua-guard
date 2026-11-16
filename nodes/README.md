# Flow nodes - records

One folder per physical board, named by its MAC (12 hex). **One shared firmware** (`firmware/flow_node_c3`) runs on all of them; nothing is copied per node.
`secret.txt` and `sticker.*` contain the pairing code and are git-ignored (`.gitignore`); `info.md` and `boot.txt` (code masked) are tracked.

| MAC | Folder | Node no. | Name | Location | Status |
|---|---|---|---|---|---|
| 44:BD:8D:5D:EB:88 | `44BD8D5DEB88` | 1 | main | | flashed 2026-11-16 (COM40), hw_test passed; paired as **Node 1** after the registry re-add (it was meant to replace the dead Node 2 but was added without Replace) |
| 84:C7:BB:FC:41:A4 | `84C7BBFC41A4` | 2 | left | | paired and online (COM37); **Node 2** since the 2026-11-16 re-add (was Node 4) |
| 44:7B:30:88:6C:F8 | `447B30886CF8` | 3 | Right | | paired and online (COM36); **Node 3** since 2026-11-16 (was Node 5) |
| 84:C7:BB:FC:5D:00 | `84C7BBFC5D00` | 4 | right sub | | paired and online (COM34); **Node 4** since 2026-11-16 (was Node 3) |
| 44:7B:30:85:4B:88 | `447B30854B88` | - | (was Main outlet) | | **retired - damaged C3, replaced on 2026-11-16 by 44:BD:8D:5D:EB:88** (history on the gateway stays under its old number) |

Numbers are assigned by the gateway registry in the order boards are added; they changed on 2026-11-16 (see docs/HANDOFF.md 2e). The pipeline is bound by MAC.

Procedure for a new board: flash `firmware/hw_test_c3`, check boot / LED / BOOT / pulses, flash `firmware/flow_node_c3`, record the banner, print the sticker (`tools/make_node_labels.py`), add the node from the gateway page, then fill in number, name and location here.
After a flash, finish with a **watchdog reset** (`esptool --after watchdog-reset`): the normal RTS reset leaves these boards in ROM download mode.

## Rain node

| MAC | Folder | Node no. | Name | Status |
|---|---|---|---|---|
| 30:76:F5:F7:C0:38 | `3076F5F7C038` | (assigned on pairing) | Rain node | flashed 2026-11-15 (`firmware/rain_node`, DRY-RUN), waiting to be added on the gateway page (type rain) |
