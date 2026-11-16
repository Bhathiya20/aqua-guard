"""Remove simulator data (gateway 'gw-sim' and everything hanging off it) from an AquaGuard database.

Usage:  python cleanup_sim_data.py [--db PATH] [--apply]
Without --apply it only prints what would be removed.  With --apply it first writes a timestamped backup
(SQLite online backup, next to the database), deletes in ONE transaction and prints before/after counts.
Real gateway/nodes are never touched: only rows tied to the gateway named below (or its nodes) are deleted.
"""
import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

SIM_GATEWAY = "gw-sim"
DEFAULT_DB = Path(__file__).parent / "database" / "aquaguard.db"
TABLES = ["gateways", "nodes", "flow_samples", "node_sessions", "node_hw_history", "alerts",
          "gateway_heartbeats", "pairing_events_v2", "gateway_commands", "devices", "zone_state", "zone_buckets"]


def counts(c):
    return {t: c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in TABLES}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    c = sqlite3.connect(a.db)
    gw = c.execute("SELECT id FROM gateways WHERE gateway_id=?", (SIM_GATEWAY,)).fetchone()
    before = counts(c)
    if not gw:
        print("no simulator gateway in this database; nothing to do")
        return 0
    gid = gw[0]
    nodes = [r[0] for r in c.execute("SELECT id FROM nodes WHERE gateway_ref=?", (gid,))]
    ph = ",".join("?" * len(nodes)) or "NULL"
    stmts = [
        (f"DELETE FROM flow_samples WHERE node_ref IN ({ph})", nodes),
        (f"DELETE FROM node_sessions WHERE node_ref IN ({ph})", nodes),
        (f"DELETE FROM node_hw_history WHERE node_ref IN ({ph})", nodes),
        (f"DELETE FROM alerts WHERE node_ref IN ({ph}) OR alert_key LIKE ? OR message LIKE ?",
         nodes + [f"GATEWAY_OFFLINE:{gid}", f"%{SIM_GATEWAY}%"]),
        ("DELETE FROM gateway_heartbeats WHERE gateway_id=?", [SIM_GATEWAY]),
        ("DELETE FROM pairing_events_v2 WHERE gateway_ref=?", [gid]),
        ("DELETE FROM gateway_commands WHERE gateway_ref=?", [gid]),
        ("DELETE FROM devices WHERE device_key=?", [f"gateway:{SIM_GATEWAY}"]),
        ("DELETE FROM nodes WHERE gateway_ref=?", [gid]),
        ("DELETE FROM gateways WHERE id=?", [gid]),
    ]
    if not a.apply:
        print("DRY RUN (use --apply). Current counts:")
        for t, n in before.items():
            print(f"  {t:20s}{n}")
        print(f"simulator gateway id={gid}, node rows={nodes}")
        return 0
    backup = a.db.with_name(f"{a.db.stem}.backup-{datetime.now():%Y%m%d-%H%M%S}{a.db.suffix}")
    dst = sqlite3.connect(backup)
    c.backup(dst)
    dst.close()
    print(f"backup written: {backup} ({backup.stat().st_size} bytes)")
    with c:
        for sql, args in stmts:
            c.execute(sql, args)
    after = counts(c)
    print(f"{'table':20s}{'before':>9s}{'after':>9s}")
    for t in TABLES:
        print(f"{t:20s}{before[t]:>9d}{after[t]:>9d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
