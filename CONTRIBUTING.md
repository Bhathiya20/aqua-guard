# Contributing

## Workflow
1. Update: `git checkout main && git pull`.
2. Branch: `git checkout -b feature/<short-name>` (or `fix/<short-name>`).
3. Make small commits with clear messages.
4. **Run the tests before you push:** `npm test` (pytest, vitest, firmware host tests, portal tests, Playwright). Everything must pass.
5. Push the branch and open a pull request into `main`. Someone else reviews it; do not push to `main` directly.

## Rules
* **Never commit secrets.** That means `aqua_secrets.h`, `.env`, `*.db`, `nodes/*/secret.txt`, sticker images, pairing codes, Wi-Fi names or passwords. They are in `.gitignore`; check `git status` before every commit.
* Do not regenerate secrets (`tools/generate_secrets.py`).
* Gateway firmware: flash only with `node scripts/flash_gateway.mjs` (see `docs/FIRMWARE_FLASHING.md`).
* Do not run the simulator against the real database. Use `npm run demo`.
* Keep the valve in dry-run unless the owner says otherwise.
* To send the project to someone outside the team, use `node scripts/make_share_copy.mjs` (it leaves all secrets out and checks the zip).
