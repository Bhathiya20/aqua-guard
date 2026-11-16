"""End-to-end: real node-link + gateway-core C++ code (lossy simulated ESP-NOW, hotspot channel change, gateway
outage, node reboot) -> the gateway's actual MQTT JSON -> Flask MQTT handler -> SQLite -> REST API."""
import json
import random
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import database
import mqtt_service
import services
from database import db_session
from tests.conftest import ADMIN_EMAIL, ADMIN_PW, login

ROOT = Path(__file__).resolve().parents[3]            # .../Final system_____________________
FW = ROOT / "tests" / "firmware"
LIB = ROOT / "libraries" / "AquaProtocol" / "src"
CLANG = shutil.which("clang++") or shutil.which("g++")
BASE = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

pytestmark = pytest.mark.skipif(CLANG is None, reason="no C++ compiler available")


@pytest.fixture(scope="module")
def sim_output(tmp_path_factory):
    exe = tmp_path_factory.mktemp("sim") / ("sim_export.exe" if sys.platform == "win32" else "sim_export")
    r = subprocess.run([CLANG, "-std=c++17", "-O1", f"-I{LIB}", f"-I{FW}", str(FW / "sim_export.cpp"), "-o", str(exe)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = subprocess.run([str(exe), str(int(BASE.timestamp())), "600", "20"], capture_output=True, text=True, timeout=300)
    assert out.returncode == 0
    return out.stdout.splitlines()


def parse(lines):
    msgs, truth, stats = [], [], None
    for ln in lines:
        if ln.startswith("MSG "):
            _, ms, topic, js = ln.split(" ", 3)
            msgs.append((int(ms), topic, js))
        elif ln.startswith("TRUTH "):
            _, node, boot, pulses = ln.split()
            truth.append((int(node), int(boot, 16), int(pulses)))
        elif ln.startswith("STATS"):
            stats = ln
    return msgs, truth, stats


def replay(cfg, msgs, duplicate=0.2, seed=7):
    """Feed MQTT messages to the real handler in sim-time order; evaluate alerts every 2 sim seconds.
    ~20 % of messages are delivered twice (QoS1 redelivery); results are collected."""
    rnd = random.Random(seed)
    results, states, next_eval = [], [], 0
    for ms, topic, js in msgs:
        now = BASE + timedelta(milliseconds=ms)
        while next_eval <= ms:
            with db_session() as c:
                services.evaluate_alerts(c, cfg, BASE + timedelta(milliseconds=next_eval))
            c = database.get_db(); states.append((next_eval, services.leak_state(c)["state"])); c.close()
            next_eval += 2000
        results.append(mqtt_service.handle_message(cfg, topic, js.encode(), now))
        if rnd.random() < duplicate:
            results.append(mqtt_service.handle_message(cfg, topic, js.encode(), now))
    return results, states


def test_full_path_volume_exact_and_leak_lifecycle(app, cfg, sim_output):
    msgs, truth, stats = parse(sim_output)
    print("\n" + stats)
    assert len(msgs) > 300
    results, states = replay(cfg, msgs)
    assert results.count("duplicate") > 50 and results.count("rejected") == 0

    c = database.get_db()
    # ---- volume: per (node, boot) the DB total equals the last cumulative counter that reached it ----------
    for node, boot, true_pulses in truth:
        row = c.execute("SELECT MAX(cum_pulses) m, SUM(delta_pulses) d, COUNT(*) n FROM flow_samples WHERE node_id=? AND boot_id=?", (node, boot)).fetchone()
        assert row["n"] > 50
        assert row["d"] == row["m"], f"node{node}: summed deltas {row['d']} != last cumulative counter {row['m']}"
        assert 0 <= true_pulses - row["m"] <= 450 * 3, f"node{node}: {true_pulses - row['m']} pulses not yet delivered at end of run"
    # the reboot kept history: node 1 has two epochs and total = sum of both
    boots = {r[0] for r in c.execute("SELECT DISTINCT boot_id FROM flow_samples WHERE node_id=1")}
    assert len(boots) == 2
    total1 = services.consumption_total(c, 1)["liters"]
    truth1 = sum(p for n, b, p in truth if n == 1) / 450.0
    assert truth1 - 1.5 <= total1 <= truth1 + 0.001
    # ---- no duplicated rows ---------------------------------------------------------------------------------
    dup = c.execute("SELECT COUNT(*) FROM (SELECT 1 FROM flow_samples GROUP BY node_id,boot_id,seq HAVING COUNT(*)>1)").fetchone()[0]
    assert dup == 0

    # (the leak lifecycle of the old two-node detector is gone; leak detection is covered by test_analysis_service / test_simulator_v2)
    # ---- gateway/comm diagnostics reached the DB -----------------------------------------------------------------------
    comm = c.execute("SELECT MAX(recovered) r, MAX(duplicates) d FROM node_comm_health WHERE node_id=1").fetchone()
    assert comm["r"] > 0 and comm["d"] > 0
    assert c.execute("SELECT COUNT(*) FROM gateway_heartbeats").fetchone()[0] >= 30
    c.close()


def test_api_serves_replayed_data_to_dashboard(app, cfg, sim_output):
    msgs, truth, _ = parse(sim_output)
    replay(cfg, msgs)
    cl = app.test_client(); login(cl)
    ws = cl.get("/api/water/summary").get_json()
    assert ws["node1TotalLiters"] > 50 and ws["node2TotalLiters"] > 40 and ws["node1TotalLiters"] > ws["node2TotalLiters"]
    hist = cl.get("/api/water/history?node=1&limit=20").get_json()
    assert len(hist) == 20 and hist == sorted(hist, key=lambda r: r["measuredAt"])
    assert cl.get("/api/leaks/events").get_json() == []                      # legacy two-node data has no pipeline: no events, and no fake ones
    assert cl.get("/api/devices").get_json()["devices"][0]["sessions"] == 0         # v2 telemetry no longer touches the retired v1 device rows (intentional change)
