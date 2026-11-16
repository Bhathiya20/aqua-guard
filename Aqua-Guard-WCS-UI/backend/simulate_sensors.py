"""Realistic multi-node telemetry simulator (schema 2) for UI / backend testing without hardware.

  python simulate_sensors.py --direct              # write straight into the local database (backend need not run MQTT)
  python simulate_sensors.py --mqtt                # publish to the configured MQTT broker (uses backend/.env)

Options: --demo (use .demo/sim.db; the simulator refuses to run unless AQUAGUARD_DB_PATH is a demo database), --leak (node 2 reads less than node 1 for a while), --loss 0.1 (drop 10 % of packets),
         --rain (simulate a rain event), --speed 5 (time acceleration), --seconds 600

N nodes on a pipeline (protocol v2, see sim_v2.py):
  python simulate_sensors.py --pipeline net.json --leak-at p1 --leak-lpm 1.5 --seconds 1800      # fast, virtual clock, then prints the analysis
  python simulate_sensors.py --pipeline net.json --silent 2 --seconds 1800                       # node 2 never reports (missing sensor)
  python simulate_sensors.py --pipeline net.json --reset 1:900 --seconds 1800                    # node 1 reboots after 15 min (counter reset)
  python simulate_sensors.py --pipeline net.json --start 2026-01-01T01:30 --leak-at p2 --leak-lpm 0.6 --seconds 9600   # night flow
  --pipeline FILE   pipeline JSON (the exported design); --start ISO-UTC (default 12:00 today); --late 180 retransmits silent windows 180 s later
  --silent N[:from-to]  node N sends nothing (optionally only between those run seconds)   --reset N:sec   node N reboots at that second
  --unsynced  gateway clock not synchronised   --realtime  publish at real speed instead (use with --mqtt)
It creates the registry, the pipeline (if none is active) and the analysis result in the local database.  SIMULATION, not hardware evidence.
This is a SIMULATION - it is not evidence that hardware works.
"""
import argparse
import json
import random
import time
import uuid

from config import load_settings
import mqtt_service


def run_pipeline(a):
    """N-node protocol-v2 scenario on a pipeline document (virtual clock unless --realtime)."""
    from datetime import datetime, timezone
    import sim_v2
    import analysis_service
    cfg = load_settings()
    import database
    database.set_db_path(cfg.db_path)
    database.init_db(cfg.default_admin_email, cfg.default_admin_password)
    doc = json.loads(open(a.pipeline, encoding="utf-8").read())
    plant = sim_v2.Plant(doc, leaks=[{"part": p, "lpm": a.leak_lpm, "start": 0} for p in a.leak_at], tz_min=analysis_service.params_from_env().tz_offset_min)
    t0 = int(datetime.fromisoformat(a.start).replace(tzinfo=timezone.utc).timestamp()) if a.start else int(time.time() // 86400 * 86400) + 12 * 3600
    f = sim_v2.Faults(unsynced=a.unsynced)
    for sp in a.silent:
        num, _, rng = sp.partition(":")
        lo, _, hi = rng.partition("-")
        f.silent.setdefault(int(num), []).append((int(lo or 0), int(hi or a.seconds)))
        if a.late is not None:
            f.late[int(num)] = a.late
    for rs in a.reset:
        num, _, at = rs.partition(":")
        f.resets.setdefault(int(num), []).append(int(at))
    if a.loss:
        f.loss = a.loss
    if a.realtime:
        raise SystemExit("--realtime needs --mqtt publishing; use the virtual mode (default) for the analysis scenarios")
    print(f"[SIM] {len(plant.nodes)} nodes {sorted(plant.nodes)} on '{doc.get('name', 'pipeline')}', {a.seconds}s of virtual time starting {datetime.fromtimestamp(t0, timezone.utc):%Y-%m-%d %H:%M} UTC")
    n = sim_v2.deliver_all(cfg, plant, t0, a.seconds, f, a.step)
    print(f"[SIM] {n} node messages ingested")
    with database.db_session() as conn:
        if not conn.execute("SELECT 1 FROM pipelines WHERE deleted=0 AND active_version IS NOT NULL").fetchone():
            print("[SIM] no active pipeline in the database: import this one in the web app (Pipeline > Import) to see it there")
        res = analysis_service.run_analysis(conn, cfg, datetime.fromtimestamp(t0 + a.seconds, timezone.utc))
    for r in res:
        print(f"[ANALYSIS] pipeline {r['pipelineId']} v{r['version']}")
        for z in r["zones"]:
            b = z["balance"]
            bal = f"Qin {b['qIn']} Qout {b['qOut']} unaccounted {b['unaccounted']} L/min" if b else "no complete bucket"
            print(f"   {z['name']:<28} {z['class']:<14} {z['status']:<14} {bal}")
    if not res:
        print("[ANALYSIS] no active pipeline: save/activate one in the web app, then re-run")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mqtt", action="store_true")
    ap.add_argument("--demo", action="store_true", help="create/use .demo/sim.db (the simulator never writes into the real database)")
    ap.add_argument("--direct", action="store_true")
    ap.add_argument("--leak", action="store_true")
    ap.add_argument("--rain", action="store_true")
    ap.add_argument("--loss", type=float, default=0.0)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--seconds", type=int, default=600)
    ap.add_argument("--pipeline")
    ap.add_argument("--start")
    ap.add_argument("--leak-at", action="append", default=[])
    ap.add_argument("--leak-lpm", type=float, default=1.5)
    ap.add_argument("--silent", action="append", default=[])
    ap.add_argument("--reset", action="append", default=[])
    ap.add_argument("--late", type=int)
    ap.add_argument("--unsynced", action="store_true")
    ap.add_argument("--realtime", action="store_true")
    ap.add_argument("--step", type=int, default=5)
    a = ap.parse_args()
    import sim_guard
    sim_guard.require_demo_db(a.demo, a.mqtt)          # before any settings/database are touched
    if a.pipeline:
        return run_pipeline(a)
    if not (a.mqtt or a.direct):
        a.direct = True

    cfg = load_settings()
    import database
    database.set_db_path(cfg.db_path)
    database.init_db(cfg.default_admin_email, cfg.default_admin_password)
    prefix = cfg.mqtt_topic_prefix
    client = None
    if a.mqtt:
        import paho.mqtt.client as mqtt
        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id=f"aquaguard-sim-{uuid.uuid4().hex[:6]}")
        if cfg.mqtt_user:
            client.username_pw_set(cfg.mqtt_user, cfg.mqtt_password)
        if cfg.mqtt_tls:
            client.tls_set(ca_certs=cfg.mqtt_ca_file or None)
        client.connect(cfg.mqtt_host, cfg.mqtt_port, 60)
        client.loop_start()

    def publish(sub, obj):
        payload = obj if isinstance(obj, str) else json.dumps(obj)
        if client:
            client.publish(f"{prefix}/{sub}", payload, qos=1)
        else:
            mqtt_service.handle_message(cfg, f"{prefix}/{sub}", payload.encode())

    rnd = random.Random()
    boot = {1: rnd.randrange(1, 2 ** 32), 2: rnd.randrange(1, 2 ** 32), 3: rnd.randrange(1, 2 ** 32)}
    seq = {1: 0, 2: 0, 3: 0}
    cum = {1: 0, 2: 0}
    level, raining = 35.0, False
    t0 = time.time()
    print(f"[SIM] publishing {'to MQTT' if client else 'directly into the database'}; Ctrl+C to stop")
    try:
        step = 0
        while step * 2 < a.seconds:
            step += 1
            el = step * 2
            demand = 6.0 + 3.0 * (1 if (el // 90) % 2 else 0)               # L/min, varies slowly
            leak = a.leak and 60 <= el % 400 < 300
            for node in (1, 2):
                lpm = demand - (1.8 if (node == 2 and leak) else 0.0) + rnd.uniform(-0.15, 0.15)
                lpm = max(lpm, 0.0)
                pulses = int(round(lpm * 450 / 60 * 2))
                cum[node] += pulses
                seq[node] += 1
                if rnd.random() < a.loss:
                    continue                                                  # packet lost on the radio link
                publish(f"node{node}", {
                    "schema": 2, "node": node, "boot": boot[node], "seq": seq[node], "recovered": False, "backlog": False,
                    "flowRate": round(pulses / 450 * 30, 3), "totalLiters": round(cum[node] / 450, 3),
                    "intervalLiters": round(pulses / 450, 3), "intervalPulses": pulses, "cumPulses": cum[node],
                    "pulsesPerLiter": 450.0, "intervalMs": 2000, "uptime": el, "status": 0,
                    "rxEpoch": int(time.time()), "channel": 6, "rssi": -52 - node})
            if a.rain and (el // 120) % 3 == 1:
                raining = True
            elif a.rain:
                raining = False
            if raining and level < 94:
                level += 0.3
            elif not raining and level > 20:
                level -= 0.02
            seq[3] += 1
            publish("rainwater", {
                "schema": 2, "node": 3, "boot": boot[3], "seq": seq[3], "recovered": False, "backlog": False, "rain": raining,
                "waterLevel": round(level, 1), "levelValid": True, "distance": round((100 - level) * 0.3, 1),
                "turbidity": 3300 if not raining or el % 240 > 40 else 2000, "turbidityValid": True,
                "waterClear": not (raining and el % 240 < 40), "tankFull": level >= 95, "valveOpen": raining and level < 95,
                "valveState": 3 if raining and level < 95 else 0, "valveReason": 4 if raining else 0, "buzzer": False,
                "firstFlush": False, "dryRun": True, "faults": 0, "rainEvents": 1 if raining else 0, "uptime": el,
                "rxEpoch": int(time.time()), "channel": 6, "rssi": -58})
            if step % 8 == 0:
                node_stats = [{"node": n, "bound": True, "ageS": 1, "lastSeenEpoch": int(time.time()), "boot": boot[n],
                               "received": seq[n], "duplicates": int(seq[n] * 0.05), "recovered": int(a.loss * seq[n] * 0.6),
                               "lost": int(a.loss * seq[n] * 0.1), "missing": 0, "outOfOrder": 1, "nacksSent": 2, "sessions": 1,
                               "invalid": 0, "cumAck": seq[n], "nodeUptime": el, "nodeStatus": 0, "nodeRetransmits": int(a.loss * seq[n]),
                               "nodeDropped": 0, "nodeRediscoveries": 0, "rssi": -55} for n in (1, 2, 3)]
                publish("gateway/heartbeat", {"schema": 2, "gateway": "gw-sim", "uptime": el, "epoch": int(time.time()),
                                              "wifi": {"up": True, "rssi": -58, "channel": 6, "reconnects": 0, "channelChanges": 0},
                                              "mqtt": {"up": True, "reconnects": 1, "published": step * 3, "publishFail": 0},
                                              "queue": {"depth": 0, "capacity": 256, "overflow": 0}, "freeHeap": 148000,
                                              "rx": {"frames": step * 3, "rejected": 0, "badTag": 0, "badValue": 0, "unknownMac": 0, "acks": step * 3, "nacks": 0},
                                              "nodes": node_stats})
            if not client:                                                   # direct mode: also run the evaluator
                mqtt_service.Evaluator(cfg).tick()
            time.sleep(2.0 / a.speed)
    except KeyboardInterrupt:
        pass
    print(f"[SIM] done after {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
