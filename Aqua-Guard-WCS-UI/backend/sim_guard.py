"""Safety guard for the simulators: they may only write into a demo / scratch database, never into the real one.

A database counts as a demo database when its path contains a ".demo" / ".tmp" / "scratch" folder or its file name contains "demo", "sim", "e2e" or "test".
`--demo` creates one (<repo>/.demo/sim.db) and points AQUAGUARD_DB_PATH at it before the settings are loaded.
"""
import os
import sys
from pathlib import Path

DEMO_DIRS = {".demo", ".tmp", "scratch"}
DEMO_WORDS = ("demo", "sim", "e2e", "test")


def is_demo_db(path) -> bool:
    p = Path(path)
    return bool(DEMO_DIRS & {x.lower() for x in p.parts}) or any(w in p.name.lower() for w in DEMO_WORDS)


def require_demo_db(demo_flag: bool = False, mqtt: bool = False) -> None:
    """Call before load_settings().  Exits with a clear message unless the target database is a demo one."""
    if mqtt and os.environ.get("AQUAGUARD_SIM_ALLOW_MQTT") != "1":
        sys.exit("refusing --mqtt: it would publish simulated telemetry to the real MQTT broker. Use --direct (demo database), "
                 "or set AQUAGUARD_SIM_ALLOW_MQTT=1 if you point at a private broker.")
    if demo_flag and not os.environ.get("AQUAGUARD_DB_PATH"):
        d = Path(__file__).resolve().parents[2] / ".demo"
        d.mkdir(exist_ok=True)
        os.environ["AQUAGUARD_DB_PATH"] = str(d / "sim.db")
    path = os.environ.get("AQUAGUARD_DB_PATH")
    if not path:
        sys.exit("refusing to run: the simulator would write into the REAL database. Set AQUAGUARD_DB_PATH to a demo database "
                 "(e.g. .demo/sim.db) or pass --demo to create one. `npm run demo` does this for you.")
    if not is_demo_db(path):
        sys.exit(f"refusing to run: AQUAGUARD_DB_PATH={path} does not look like a demo database "
                 "(needs a .demo/.tmp/scratch folder or demo/sim/e2e/test in the file name).")
