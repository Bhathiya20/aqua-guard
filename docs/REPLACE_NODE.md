# Replace a flow node (Node 2, "Main outlet") with a new ESP32-C3

Situation: the ESP32-C3 of **Node 2** (old MAC `44:7B:30:85:4B:88`) is damaged. The node is marked **out of service** on the Nodes page (grey badge, no offline / missing-packet alerts, its zone is left out of the leak check and says so). The new board takes over the **same Node 2 number, name "Main outlet", history and place in the pipeline** through **Replace**. Nothing is deleted.

The sensor (YF-S201), the 5 V buck, the 1 k pull-up and the wiring stay as they are. Only the C3 is exchanged. If you are not sure the old wiring is intact, test it first (step 0).

## How not to damage the board (read first)

- [ ] **Never power the C3 from USB and from the 3.3 V regulator at the same time.** While it is on USB, the 3.3 V regulator must be **disconnected** (the 3V3 pin of the C3 must not be fed by anything else). Same rule for the 5 V pin.
- [ ] **Set the 5 V buck to 5.0 V with a multimeter before it is connected** to the sensor or the board. Adjust the trimmer with nothing attached, and re-measure after any adjustment.
- [ ] **Check for shorts before the first power-up** (multimeter in continuity/resistance mode, board unpowered): 3V3 to GND, 5 V to GND, and GPIO4 to GND must **not** be a short (GPIO4 to GND should read about 1 k to 3V3 through the pull-up, never ~0 ohm).
- [ ] **The sensor signal never goes above 3.3 V on GPIO4**: the 1 k pull-up goes to **3.3 V**, never to 5 V. The sensor's red wire is 5 V, yellow is the signal, black is GND, and the grounds of the buck, the sensor and the C3 are common.
- [ ] No loose strands, no bare wires touching in the enclosure; no wet hands or water on the bench while the board is powered.
- [ ] Connect and disconnect wires only with **everything unpowered** (USB unplugged, regulator off).
- [ ] Use a good data USB cable. If the board gets hot, smells or the PC does not see it: unplug at once.
- [ ] Do not "fix" a dead board by reflashing in a loop; measure first (3V3 pin about 3.3 V, no short).

## 0. Before flashing (bench, nothing connected to USB)

1. Look at the new board: no bent pins, no solder bridges, no burnt parts.
2. Measure with the multimeter, unpowered: 3V3 to GND and 5 V to GND are not shorted.
3. Fit the board into the circuit as before (see `docs/WIRING_AND_SAFETY.md`, flow node section), but **leave the 3.3 V regulator disconnected** and **leave the buck/sensor supply off** while it is connected to USB.

## 1. Flash (one board, regulator disconnected, USB only)

All commands run in the project root `Final system_____________________`. **Never run `tools/generate_secrets.py`**: `libraries/AquaProtocol/src/aqua_secrets.h` is shared by every board and must stay exactly as it is (the new board must get the **same** `aqua_secrets.h` as the gateway and all other nodes, otherwise it cannot talk to them). It is not committed; do not regenerate it, do not edit it.

1. Plug the new C3 in by USB. Find its COM port (Device Manager, or `py -m esptool --port COMxx read_mac`). **The MAC is the identity, not the COM number.** Write the MAC down: **it is not `44:7B:30:85:4B:88`** (that is the damaged board).
2. **Hardware test first** (`firmware/hw_test_c3`, no radio, no NVS, no pairing code):
   - compile and upload with the FQBN `esp32:esp32:esp32c3:CDCOnBoot=cdc`, for example
     `arduino-cli compile --upload -p COMxx --fqbn esp32:esp32:esp32c3:CDCOnBoot=cdc --libraries libraries firmware/hw_test_c3`
     (the repository scripts use the pinned toolchain from `node scripts/setup_firmware_toolchain.mjs`; `scripts/compile_firmware.mjs` is the same idea),
   - open a serial monitor at 115200 baud. Check: `[BOOT]` shows the new MAC, GPIO4 idles **HIGH** (about 3.0 to 3.3 V), the LED lights while the log says "pin HIGH", `BOOT` shows PRESSED / RELEASED when you press it, and when you blow/shake water through the sensor the pulse counter rises and the pin swings HIGH and LOW.
   - If any of this fails: stop, do not flash the real firmware, check wiring and the multimeter checks above.
3. **Real firmware**: compile and upload `firmware/flow_node_c3` (battery-none build, TX-power support, one shared firmware, no Wi-Fi credentials inside):
   `arduino-cli compile --upload -p COMxx --fqbn esp32:esp32:esp32c3:CDCOnBoot=cdc --libraries libraries firmware/flow_node_c3`
4. **Watchdog reset after flashing**: `py -m esptool --port COMxx --after watchdog-reset read_mac`. (The COM port disappears for a few seconds and comes back; that is normal.)
5. Read the boot banner on the serial monitor (115200). It shows the MAC, the pairing code and a QR payload. **Write the code down from the banner or the sticker; do not paste it into chat, a commit or a log.** Expected after a few seconds: `JOINING`/scan, then (once paired) `PAIRED ch=9 ... batt=none`.
6. Create the node folder `nodes/<MAC12>/` (MAC without colons, upper case) with an `info.md` like the other nodes (MAC, board, COM port used, firmware commit, hw_test result, "Node 2 / Main outlet (replacement)"). The code and sticker files are git-ignored (`secret.txt`, `sticker.*`); `boot.txt` has the code masked. The sticker can be printed with `python tools/make_node_labels.py --serial-log boot.txt`.

## 2. Replace Node 2 on the gateway

The **old** MAC is replaced by the **new** MAC under the same number: the history, name "Main outlet", calibration and the pipeline place stay.

Option A - gateway page (the gateway is on the home network):
1. Open **http://192.168.1.11** and sign in with the gateway admin password.
2. In the node list tap **Node 2 (Main outlet)**. Under **Replace hardware** enter the **new MAC** (`AA:BB:CC:DD:EE:FF`) and the **new pairing code** (or scan the new sticker QR), then press **Replace** and confirm.
3. Power the new node on its normal supply (see step 4 below). It should be accepted within a few seconds; the page shows it paired.

Option B - backend UI: **Nodes -> Node 2 -> ⋯ -> Replace**, enter the new MAC and code. Same result (a signed command goes to the gateway).

The pairing code is only typed here; the backend never stores it.

## 3. Take Node 2 out of "out of service"

1. **Nodes -> Node 2 -> ⋯ -> "Back in service"** (the menu item changes its label while the flag is on). The grey badge turns into the normal online/offline state.
2. Until you do this the node raises **no** offline or missing-packet alerts and the Leak detection page / Overview / status strip show a note "... not monitored: Node 2 out of service". After you do it, the zones that depend on Node 2 are checked again.
3. If you replace a node and forget this step, the new node works but stays silent about problems. Do not forget it.

## 4. Connect to the real supply (after flashing and Replace)

1. USB **unplugged**.
2. Buck output measured **5.0 V** with nothing connected; then connect the sensor.
3. Connect the 3.3 V regulator to the C3 (only now; USB stays unplugged). Power on.

## 5. What to check afterwards

- [ ] **Nodes page**: Node 2 "Main outlet", **online**, type flow, **No battery**, **TX 8.5 dBm**, RSSI a real value (not "—"), the **new MAC** listed under hardware history (the old one stays in the history).
- [ ] **Back in service** done (no grey "out of service" badge).
- [ ] Open water briefly: flow on Node 2 appears within seconds and **the total changes** (L/min and "today").
- [ ] **Pipeline**: Node 2 is still placed on its pipe (Live view shows its flow); **Leak detection** is green with no "paused" note. If the leak check is amber ("paused"), the node is not reporting yet.
- [ ] **Alerts**: no active offline / missing-packet alert for Node 2. A few missing packets right after power-up are normal.
- [ ] **Link**: Nodes -> Node 2 -> TX power shows missing % and RSSI. If the missing % stays above about 5 percent, try another position or a higher TX level and compare (see `docs/HANDOFF.md`, link test).
- [ ] The old board `44:7B:30:85:4B:88` must not appear online again (the gateway no longer accepts it).
- [ ] Commit `nodes/<MAC12>/info.md` and update `docs/HANDOFF.md`.
