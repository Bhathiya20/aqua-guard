# Aqua Guard

Aqua Guard is a water-monitoring and leak-detection system. Flow nodes measure the water running through each pipe section, a rain node watches a rainwater tank, and a web dashboard shows live flow, leak balance, usage limits and alerts.

## Architecture

```
Flow nodes (ESP32-C3, YF-S201)  ─┐
Rain node (ESP32)               ─┴─ ESP-NOW ─ Gateway (ESP32) ─ Wi-Fi / MQTT ─ Backend (Flask + SQLite) ─ Web (React)
```

* **Nodes** count pulses and send them over ESP-NOW (authenticated packets, ACK/retransmit; volume comes from a cumulative counter so lost packets lose no water).
* **Gateway** joins the Wi-Fi, owns the node registry and pairing, serves a small setup portal, and publishes to MQTT.
* **Backend** stores readings, runs the leak balance (D = Q in − ΣQ out), the per-node usage checks (prolonged / continuous / night flow), alerts and history.
* **Web** shows overview, water monitoring, leak detection, nodes, alerts (Active / History), analytics and usage settings.

## Folder map

| Folder | What is in it |
|---|---|
| `Aqua-Guard-WCS-UI/` | The web app (`src/`, React + Vite) and the backend (`backend/`, Flask, tests) |
| `firmware/gateway`, `flow_node_c3`, `rain_node`, `hw_test_c3`, `rain_hwtest` | ESP32 sketches (`partitions.csv` is in `gateway/`) |
| `libraries/AquaProtocol/` | Shared firmware library: protocol, node link, registry, flow meter, valve |
| `scripts/` | `run-tests`, `demo`, `setup`, firmware toolchain, `flash_gateway.mjs`, `flash_node.mjs`, `make_share_copy.mjs` |
| `tools/` | `serial_capture.py`, `make_node_labels.py`, QR/secret helpers and their tests |
| `tests/` | Shared test vectors used by the firmware host tests |
| `docs/` | SPEC, HANDOFF, PAIRING, CALIBRATION, WIRING_AND_SAFETY, REPLACE_NODE, TEST_PROCEDURE, WATER_TEST, SETUP_DEV, FIRMWARE_FLASHING |
| `nodes/` | One record per physical board (by MAC) |

## Quick start

Needs Node.js 20+ and Python 3.10+ (see `docs/SETUP_DEV.md` for a fresh Windows laptop).

```powershell
npm install
npm run setup     # Python venv, backend deps, web deps
npm run demo      # self-contained demo: own fresh database, no MQTT, simulated leak (http://127.0.0.1:5273)
npm run dev       # real app: API on :5000, web on :5173
npm test          # pytest + vitest + firmware host tests + portal tests + Playwright e2e
```

## Secrets: read this

Three kinds of files are **never** in git and are **not** in this repository:

* `libraries/AquaProtocol/src/aqua_secrets.h` (radio keys), `Aqua-Guard-WCS-UI/backend/.env`, the real database, `nodes/*/secret.txt` and sticker images.

The project owner shares them **privately and directly** (not through GitHub, not through WhatsApp or other group chats). Copy them into the same paths. Team members must **not** regenerate secrets (never run `tools/generate_secrets.py`): new keys would stop every flashed node from talking to the gateway. Templates: `aqua_secrets.example.h`, `backend/.env.example`.

## More

`docs/SETUP_DEV.md` · `docs/FIRMWARE_FLASHING.md` · `CONTRIBUTING.md` · `docs/WATER_TEST.md` · `docs/HANDOFF.md` (current state and known issues)
