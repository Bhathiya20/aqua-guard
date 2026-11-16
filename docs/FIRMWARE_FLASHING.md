# Flashing firmware

Prerequisite: `node scripts/setup_firmware_toolchain.mjs` and a valid `libraries/AquaProtocol/src/aqua_secrets.h` (from the project owner).

## Always read the board's MAC first
Plug in exactly one board and run `python -m esptool --port COMxx read-mac` (or let the scripts below do it). Check it against `nodes/README.md`. Find the port in Device Manager; ports change when you re-plug.

## Gateway: ONLY with the flash script
```powershell
node scripts/flash_gateway.mjs COM29
```
**Never** run `arduino-cli upload` or the Arduino IDE Upload button on the gateway. Its upload recipe writes `boot_app0.bin` at 0xE000, which is the last page of the gateway's NVS partition, and wipes the saved Wi-Fi settings and setup flag. The script writes the application only (0x10000), so Wi-Fi, admin password, MQTT settings and the node registry survive. (A permanent partition fix is a TODO after the water test, see `docs/HANDOFF.md`.)

## Flow node (ESP32-C3) and rain node
```powershell
node scripts/flash_node.mjs COM37                       # picks the sketch from the chip
node scripts/flash_node.mjs COM37 --sketch rain_node    # force a sketch
```
The script reads the MAC, refuses the protected gateway MAC, compiles into `firmware/<sketch>/build/app` and writes the app at 0x10000 only (pairing data in NVS stays). After flashing, a node should show "PAIRED ch=…" within about 40 s:
```powershell
Aqua-Guard-WCS-UIackend\.venv\Scripts\python.exe tools/serial_capture.py node=COM37 --seconds 40
```
Use the project's venv Python (it has `pyserial`); the system Python usually does not.

## Hardware tests
`firmware/hw_test_c3` and `firmware/rain_hwtest` are bring-up sketches for bench checks; compile them with `node scripts/compile_firmware.mjs <name>`.

## Safety
The rain valve output stays in DRY-RUN by default. See `docs/WIRING_AND_SAFETY.md` before connecting anything to mains or a real valve.
