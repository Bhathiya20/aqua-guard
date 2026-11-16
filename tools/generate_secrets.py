#!/usr/bin/env python3
"""Generate the per-site secrets: libraries/AquaProtocol/src/aqua_secrets.h  AND  the matching backend/.env entry.

  python tools/generate_secrets.py            # first time: random PSK / PMK / LMK / command key
  python tools/generate_secrets.py --force    # regenerate EVERYTHING (all nodes must be re-flashed, all pairings redone)
  python tools/generate_secrets.py --sync-env # rewrite backend/.env from the existing header (repair drift)

The command key is generated once and written to BOTH files, so the firmware and the backend can never drift
apart.  Keys are never printed.  The all-zero example template is refused by every firmware build
(AQUA_SECRETS_ARE_EXAMPLE), so no default or example key can end up in a real build.

Wi-Fi and MQTT settings are NOT here any more: they are entered in the gateway's setup portal and stored in its NVS.
"""
import argparse
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADER = ROOT / "libraries" / "AquaProtocol" / "src" / "aqua_secrets.h"
TEMPLATE = ROOT / "libraries" / "AquaProtocol" / "src" / "aqua_secrets.example.h"
ENV = ROOT / "Aqua-Guard-WCS-UI" / "backend" / ".env"
ENV_KEY = "AQUAGUARD_CMD_KEY"
KEYS = ("PSK", "PMK", "LMK", "CMD_KEY")


def c_array(b: bytes) -> str:
    return "{ " + ",".join(f"0x{x:02X}" for x in b) + " }"


def read_header_key(src: str, name: str):
    m = re.search(rf"#define AQUA_{name}\s+\{{([^}}]*)\}}", src)
    if not m:
        return None
    vals = [int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]{2})", m.group(1))]
    return bytes(vals) if len(vals) == 16 else None


def write_env(cmd_key: bytes, env_path: Path = ENV):
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    lines = [ln for ln in lines if not ln.strip().startswith(ENV_KEY + "=")]
    lines.append(f"{ENV_KEY}={cmd_key.hex()}")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_header(template: str, keys: dict) -> str:
    src = template.replace("#define AQUA_SECRETS_ARE_EXAMPLE 1\n", "")
    for name, val in keys.items():
        src, n = re.subn(rf"#define AQUA_{name}\s+\{{[^}}]*\}}", lambda _m, v=val, nm=name: f"#define AQUA_{nm}  {c_array(v)}", src)
        assert n == 1, name
    return src


def main(argv=None, header=HEADER, env=ENV, template=TEMPLATE) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="regenerate all keys")
    ap.add_argument("--sync-env", action="store_true", help="copy the command key from the existing header into backend/.env")
    a = ap.parse_args(argv)

    if a.sync_env:
        if not header.exists():
            print("no aqua_secrets.h to sync from")
            return 1
        key = read_header_key(header.read_text(), "CMD_KEY")
        if not key or key == bytes(16):
            print("aqua_secrets.h has no valid command key; run with --force")
            return 1
        write_env(key, env)
        print(f"Wrote {ENV_KEY} to {env} (key not displayed).")
        return 0

    if header.exists() and not a.force:
        print(f"{header} already exists; use --force to regenerate (changes ALL keys: re-flash and re-pair everything)\n"
              "or --sync-env to repair the backend/.env entry.")
        return 1
    keys = {name: secrets.token_bytes(16) for name in KEYS}
    header.write_text(build_header(template.read_text(), keys))
    write_env(keys["CMD_KEY"], env)
    print(f"Wrote {header} and {ENV_KEY} in {env} (keys randomised, not displayed).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
