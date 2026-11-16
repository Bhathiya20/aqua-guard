# Setting up a fresh Windows laptop

1. Install **Node.js 20 LTS** (nodejs.org) and **Python 3.10 or newer** (python.org, tick "Add to PATH"). Install **Git**.
2. Clone the repository and open a terminal in it.
3. Install and prepare:
   ```powershell
   npm install
   npm run setup
   ```
   `setup` creates `Aqua-Guard-WCS-UI/backend/.venv`, installs the backend and web dependencies and creates `backend/.env` from `.env.example` if it is missing.
4. **Get the secrets file from the project owner** (privately: USB stick or direct message, never GitHub or a group chat) and put it at
   `libraries/AquaProtocol/src/aqua_secrets.h`. If the owner also sends `backend/.env`, replace the generated one with it. **Do not generate your own secrets** (`tools/generate_secrets.py` is for the owner only, once).
5. Firmware toolchain (only if you will build or flash firmware; arduino-cli and the ESP32 core are pinned and installed into `.toolchain/`, which is git-ignored):
   ```powershell
   node scripts/setup_firmware_toolchain.mjs
   ```
   (The Python tools' packages, e.g. for the QR labels, are installed into the same venv by `npm run setup`.)
6. First run:
   ```powershell
   npm run demo    # safe: own database, no MQTT, no hardware needed
   npm run dev     # real app: http://localhost:5173 (API on :5000)
   npm test        # run before every push
   ```
   On the first real run the backend creates the database and the admin user from `.env`.

Tips: use the gateway's IP address instead of `aquaguard.local` on Windows (mDNS name resolution is unreliable). Opening a C3 USB serial port can reboot the node.
