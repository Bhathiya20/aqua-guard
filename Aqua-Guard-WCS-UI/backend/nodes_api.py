"""REST API: gateways, numbered nodes (mirror of the gateway registry) and signed AQC2 node commands.

Reads: any signed-in user.  Mutations: administrator + CSRF (auth.before_request).  The gateway registry is the source of truth:
a command is only SENT here; the node list changes when the gateway republishes its registry.  The pairing code is never stored
or logged: it exists only in the encrypted command line.
"""
import threading
import time
from datetime import timedelta

from flask import g, jsonify, request

import auth
import gateway_v2
import mqtt_service
from database import db_session, get_db
from ingest import fmt_ts, parse_ts, utc_now
from protocol_v2 import CommandError, DEV_FLOW, DEV_RAIN, build_aqc2, normalize_code, normalize_mac, parse_cmd_key

_ts_lock = threading.Lock()
_last_ts: dict = {}
MAX_WAIT_S = 15


class Refused(Exception):
    def __init__(self, code: str, message: str, http: int = 409):
        super().__init__(message)
        self.code, self.message, self.http = code, message, http


def _dev_type(t: str) -> int:
    if t not in ("flow", "rain"):
        raise CommandError("bad_type")
    return DEV_FLOW if t == "flow" else DEV_RAIN


def command_topic(cfg, gid: str) -> str:
    return f"{cfg.mqtt_topic_prefix.rstrip('/')}/{gid}/gateway/cmd"


def preflight(cfg, gw, op: str, now):
    """Refuse BEFORE anything is sent.  Order matters: key, gateway, time, TLS."""
    key = parse_cmd_key(cfg.cmd_key_hex)
    if key is None:
        raise Refused("command_key_missing", "Node commands are disabled: AQUAGUARD_CMD_KEY is missing, malformed or all zero. "
                      "Set it to the command key of this site (python tools/generate_secrets.py --sync-env copies it from aqua_secrets.h).", 503)
    if gw is None:
        raise Refused("no_gateway", "No gateway has been seen yet.", 409)
    st = gateway_v2.gateway_state(cfg, gw, now)
    if st["state"] != "online":
        raise Refused("gateway_offline", f"Gateway {gw['gateway_id']} is {st['state']}: no heartbeat in the last {cfg.gateway_offline_s} s. The command was not sent.")
    if not st["timeSynced"]:
        raise Refused("gateway_time_not_synced", "The gateway has not synchronised its clock yet (NTP). Signed commands are refused until it has.")
    if op in ("A", "P"):                       # the pairing code travels inside the command
        if not cfg.mqtt_tls:
            where = f" (open http://{gw['ip']} on your Wi-Fi)" if gw["ip"] else ""
            raise Refused("tls_required", "Adding or replacing a node sends its pairing code over MQTT, and MQTT currently has no verified TLS "
                          f"({cfg.mqtt_host}:{cfg.mqtt_port}, plaintext). This is refused. Pair the node from the gateway's own page instead{where}, "
                          "or move the broker to TLS with a certificate the backend and the gateway can verify.")
        if not gw["remote_add"]:
            raise Refused("gateway_refuses_remote_add", "The gateway does not accept remote add/replace right now (it needs verified TLS to the broker and synchronised time).")
    return key


def send_command(cfg, gw, op: str, dev_type: int, number: int = 0, mac=None, code=None, name=None, ppl_x10=None, flags=0, user=""):
    now = utc_now()
    key = preflight(cfg, gw, op, now)
    with _ts_lock:                              # the gateway rejects a timestamp older than the last one it accepted
        ts = max(int(now.timestamp()), _last_ts.get(gw["gateway_id"], 0))
        _last_ts[gw["gateway_id"]] = ts
    import secrets
    nonce = secrets.token_bytes(12)
    line = build_aqc2(key, op=op, dev_type=dev_type, number=number, mac=mac, ppl_x10=ppl_x10, flags=flags, name=name, code=code, ts=ts, nonce=nonce)
    with db_session() as conn:
        expire = conn.execute("SELECT COUNT(*) AS c FROM gateway_commands WHERE gateway_ref=? AND status='sent'", (gw["id"],)).fetchone()["c"]
        if expire >= 8:
            raise Refused("too_many_pending", "Too many commands are still waiting for the gateway; try again in a moment.", 429)
        cur = conn.execute("INSERT INTO gateway_commands (gateway_ref,op,dev_type,number,mac,created_at,created_by,nonce,status) VALUES (?,?,?,?,?,?,?,?, 'sent')",
                           (gw["id"], op, dev_type, number or None, mac, fmt_ts(now), user, nonce.hex()))
        cid = cur.lastrowid
        auth.audit(conn, f"node_cmd_{op}", f"gateway={gw['gateway_id']} type={dev_type} number={number} mac={mac or '-'}", user=g.user)   # never the code
    if not mqtt_service.publish_text(command_topic(cfg, gw["gateway_id"]), line):
        with db_session() as conn:
            conn.execute("UPDATE gateway_commands SET status='not_sent', result='mqtt_down', resolved_at=? WHERE id=?", (fmt_ts(utc_now()), cid))
        raise Refused("mqtt_down", "MQTT is not connected: the command was not sent.", 503)
    return cid


def gateway_outages(conn, now, offline_s: int, hours: int = 24):
    """[(start_epoch, end_epoch)] of the last `hours` in which NO gateway heartbeat reached the backend for longer than `offline_s`
    (gateway off or rebooting, Wi-Fi/MQTT down, backend down).  Packets missing in such a window are not radio loss."""
    t0 = now - timedelta(hours=hours)
    rows = conn.execute("SELECT received_at FROM gateway_heartbeats WHERE received_at>=? ORDER BY received_at", (fmt_ts(t0 - timedelta(minutes=10)),)).fetchall()
    ts = [parse_ts(r["received_at"]).timestamp() for r in rows]
    if not ts:
        return []                                          # no heartbeat ever seen in the window: the gateway's state is unknown, claim nothing
    out, prev = [], ts[0]
    for t in ts[1:] + [now.timestamp()]:
        if t - prev > offline_s:
            out.append((prev, t))
        prev = max(prev, t)
    return [(a, b) for a, b in out if b > t0.timestamp()]


def packet_loss_24h(conn, node_ref, now, offline_s: int):
    """Missing packets (sequence gaps) over 24 h, split: RADIO loss (counted in the loss %) versus gaps that fall inside a gateway outage."""
    outs = gateway_outages(conn, now, offline_s)
    rows = conn.execute("SELECT measured_at, gap_before FROM flow_samples WHERE node_ref=? AND received_at>=? ORDER BY measured_at, id",
                        (node_ref, fmt_ts(now - timedelta(hours=24)))).fetchall()
    radio = outage = 0
    prev_t = None
    for r in rows:
        gb = r["gap_before"] or 0
        if gb:
            t = parse_ts(r["measured_at"]).timestamp()
            lo = prev_t if prev_t is not None else t - 60
            if any(a < t and b > lo for a, b in outs):          # the window in which the packets went missing overlaps a gateway outage
                outage += gb
            else:
                radio += gb
        prev_t = parse_ts(r["measured_at"]).timestamp()
    return {"missed": radio, "got": len(rows), "outage": outage, "outageMin": round(sum(b - a for a, b in outs) / 60.0)}


def register(app, cfg):
    def read():
        c = get_db()
        c.execute("PRAGMA query_only=ON")
        return c

    def live_json(conn, n, state, now):
        """Live numbers for a flow node: newest flow, usage today / last 7 days, and missing packets (sequence gaps) over the last 24 h."""
        if n["type"] != "flow":
            return None
        day = fmt_ts(now.replace(hour=0, minute=0, second=0, microsecond=0))
        week = fmt_ts(now - timedelta(days=7))
        last = conn.execute("SELECT flow_lpm, cum_pulses, measured_at, received_at FROM flow_samples WHERE node_ref=? AND anomaly=0 ORDER BY measured_at DESC, id DESC LIMIT 1", (n["id"],)).fetchone()
        use = conn.execute("SELECT COALESCE(SUM(CASE WHEN measured_at>=? THEN delta_pulses*10.0/pulses_per_liter_x10 END),0) AS today,"
                           " COALESCE(SUM(delta_pulses*10.0/pulses_per_liter_x10),0) AS week FROM flow_samples WHERE node_ref=? AND measured_at>=? AND anomaly=0",
                           (day, n["id"], week)).fetchone()
        gap = packet_loss_24h(conn, n["id"], now, cfg.gateway_offline_s)
        age = None
        if last is not None:
            age = max(0.0, (now - parse_ts(last["received_at"])).total_seconds())
        fresh = last is not None and age is not None and age <= cfg.node_offline_s
        tot = gap["missed"] + gap["got"]
        return {"flowLpm": round(last["flow_lpm"], 3) if (last is not None and fresh and state == "online") else None,
                "lastFlowLpm": round(last["flow_lpm"], 3) if last is not None else None, "ageS": None if age is None else round(age, 1),
                "cumPulses": last["cum_pulses"] if last is not None else None, "todayLiters": round(use["today"], 2), "weekLiters": round(use["week"], 2),
                "missedPackets24h": gap["missed"], "receivedPackets24h": gap["got"], "lossPct24h": round(100.0 * gap["missed"] / tot, 1) if tot else 0.0,
                "gatewayOutagePackets24h": gap["outage"], "gatewayOutageMin24h": gap["outageMin"]}

    def node_json(conn, n, gw_state, now, gw_name=None):
        if n["state"] == "removed":
            state = "removed"
        elif n["state"] == "pending":
            state = "pending"
        elif gw_state != "online":
            state = "unknown"                    # a gateway outage must not make every node look dead
        elif n["out_of_service"]:
            state = "out_of_service"               # maintenance flag: not offline, not counted, no alerts
        elif n["last_seen"] and (now - parse_ts(n["last_seen"])).total_seconds() <= cfg.node_offline_s:
            state = "online"
        elif n["last_seen"] is None and n["online"]:
            state = "online"
        else:
            state = "offline"
        live = live_json(conn, n, state, now)
        hist = conn.execute("SELECT mac, first_seen, last_seen FROM node_hw_history WHERE node_ref=? ORDER BY first_seen", (n["id"],)).fetchall()
        return {"id": n["id"], "gatewayRef": n["gateway_ref"], "gatewayId": gw_name, "type": n["type"], "number": n["number"], "label": f"{'Rain' if n['type'] == 'rain' else 'Node'} {n['number']}",
                "name": n["name"], "mac": gateway_v2.fmt_mac(n["mac"]), "state": state, "registryState": n["state"], "batteryMv": n["battery_mv"], "rssi": n["rssi"] or None,
                "lastSeen": n["last_seen"], "pulsesPerLiter": (n["ppl_x10"] / 10.0) if n["ppl_x10"] else None, "txReduce": bool(n["tx_reduce"]), "txPowerDbm": (n["tx_dbm_x10"] or 85) / 10.0, "outOfService": bool(n["out_of_service"]),
                "addedEpoch": n["added_epoch"], "live": live, "hardware": [{"mac": gateway_v2.fmt_mac(h["mac"]), "firstSeen": h["first_seen"], "lastSeen": h["last_seen"]} for h in hist]}

    def gateway_json(conn, gw, now):
        st = gateway_v2.gateway_state(cfg, gw, now)
        cnt = conn.execute("SELECT COUNT(*) c FROM nodes WHERE gateway_ref=? AND state!='removed'", (gw["id"],)).fetchone()["c"]
        return {"id": gw["id"], "gatewayId": gw["gateway_id"], "fw": gw["fw"], "mode": gw["mode"], "ip": gw["ip"], "channel": gw["channel"], "rssi": gw["rssi"],
                "state": st["state"], "heartbeatAgeS": st["heartbeatAgeS"], "timeStatus": st["timeStatus"], "timeSynced": st["timeSynced"],
                "tls": bool(gw["tls"]), "tlsVerified": bool(gw["tls_verified"]), "remoteAdd": bool(gw["remote_add"]), "nodes": cnt,
                "rx": {"frames": gw["rx_frames"], "rejected": gw["rx_rejected"], "badTag": gw["rx_bad_tag"], "badValue": gw["rx_bad_value"],
                       "unknownMac": gw["rx_unknown_mac"], "queueDropped": gw["rx_queue_dropped"]},
                "queue": {"depth": gw["queue_depth"], "overflow": gw["queue_overflow"]}, "uptimeS": gw["uptime_s"], "freeHeap": gw["free_heap"],
                "registryRev": gw["registry_rev"], "registryAt": gw["registry_at"]}

    @app.get("/api/gateways")
    def gateways_list():
        conn = read()
        try:
            now = utc_now()
            gws = conn.execute("SELECT * FROM gateways ORDER BY id").fetchall()
            return jsonify(gateways=[gateway_json(conn, x, now) for x in gws],
                           mqtt={"tls": cfg.mqtt_tls, "host": cfg.mqtt_host, "port": cfg.mqtt_port, "remoteAddAllowed": bool(cfg.mqtt_tls)},
                           commandsEnabled=parse_cmd_key(cfg.cmd_key_hex) is not None)
        finally:
            conn.close()

    @app.get("/api/nodes")
    def nodes_list():
        conn = read()
        try:
            now = utc_now()
            allg = conn.execute("SELECT * FROM gateways").fetchall()
            gws = {x["id"]: gateway_v2.gateway_state(cfg, x, now)["state"] for x in allg}
            names = {x["id"]: x["gateway_id"] for x in allg}
            rows = conn.execute("SELECT * FROM nodes WHERE state!='removed' ORDER BY gateway_ref, type DESC, number").fetchall()
            return jsonify(nodes=[node_json(conn, n, gws.get(n["gateway_ref"]), now, names.get(n["gateway_ref"])) for n in rows])
        finally:
            conn.close()

    @app.get("/api/summary")
    def summary_v2():
        """One call for the Overview page: gateway, node counts, flow now / today at the main supply, leak alerts, battery summary."""
        import json as _json
        from analysis.zones import partition
        from pipeline.model import parse
        conn = read()
        try:
            now = utc_now()
            gws = conn.execute("SELECT * FROM gateways ORDER BY id").fetchall()
            prim = gateway_v2.primary_gateway(cfg, conn, now)            # the online gateway; never just "the last one in the table"
            gw = gateway_json(conn, prim, now) if prim else None
            gstate = {x["id"]: gateway_v2.gateway_state(cfg, x, now)["state"] for x in gws}
            names = {x["id"]: x["gateway_id"] for x in gws}
            rows = conn.execute("SELECT * FROM nodes WHERE state='paired' ORDER BY type DESC, number").fetchall()
            nodes = [node_json(conn, n, gstate.get(n["gateway_ref"]), now, names.get(n["gateway_ref"])) for n in rows]
            flow = [n for n in nodes if n["type"] == "flow"]
            online = [n for n in nodes if n["state"] == "online"]
            # "total" = what enters the plumbing: the inflow meter of the source zone of the active pipeline; without one, the sum of the online flow nodes (flagged)
            total = {"basis": "none", "flowLpm": None, "todayLiters": None, "weekLiters": None, "node": None, "pipelineId": None}
            pipe = conn.execute("SELECT p.id, p.name, v.doc FROM pipelines p JOIN pipeline_versions v ON v.pipeline_id=p.id AND v.version=p.active_version "
                                "WHERE p.deleted=0 AND p.active_version IS NOT NULL ORDER BY p.id LIMIT 1").fetchone()
            if pipe is not None:
                try:
                    zs = [z for z in partition(parse(_json.loads(pipe["doc"]))).zones if z.id == "zone:source" and (z.in_meter or z.out_meters)]
                except Exception:
                    zs = []
                if zs:
                    ms = [zs[0].in_meter] if zs[0].in_meter else list(zs[0].out_meters)     # everything the source delivers passes these meters
                    srcs = [next((n for n in flow if n["number"] == m.key[1]), None) for m in ms]
                    if srcs and all(x is not None and x["live"] for x in srcs):
                        fl = [x["live"]["flowLpm"] for x in srcs]
                        total = {"basis": "source_meter", "flowLpm": None if any(f is None for f in fl) else round(sum(fl), 3),
                                 "todayLiters": round(sum(x["live"]["todayLiters"] for x in srcs), 2), "weekLiters": round(sum(x["live"]["weekLiters"] for x in srcs), 2),
                                 "node": srcs[0]["number"], "pipelineId": pipe["id"]}
            if total["basis"] == "none" and flow:
                total = {"basis": "sum_of_nodes", "flowLpm": round(sum((n["live"]["flowLpm"] or 0.0) for n in flow if n["live"]), 3),
                         "todayLiters": round(sum(n["live"]["todayLiters"] for n in flow if n["live"]), 2),
                         "weekLiters": round(sum(n["live"]["weekLiters"] for n in flow if n["live"]), 2), "node": None, "pipelineId": None}
            leaks = conn.execute("SELECT state, COUNT(*) c FROM alerts WHERE kind='LEAK' AND active=1 GROUP BY state").fetchall()
            lk = {r["state"]: r["c"] for r in leaks}
            other = conn.execute("SELECT COUNT(*) c FROM alerts WHERE active=1 AND kind!='LEAK'").fetchone()["c"]
            with_b = [n for n in nodes if n["batteryMv"] is not None]
            bad = [n for n in with_b if not cfg.battery_min_plausible_mv <= n["batteryMv"] <= cfg.battery_max_plausible_mv]       # e.g. 0.2 V: floating pin / missing divider
            low = [n for n in with_b if cfg.battery_min_plausible_mv <= n["batteryMv"] < cfg.battery_low_mv]
            return jsonify(gateway=gw, gateways={"total": len(gws), "online": sum(1 for v in gstate.values() if v == "online")}, nodes={"total": len(nodes), "online": len(online), "offline": len([n for n in nodes if n["state"] == "offline"])},
                           total=total, pipeline=None if pipe is None else {"id": pipe["id"], "name": pipe["name"]},
                           leaks={"suspected": lk.get("suspected", 0), "confirmed": lk.get("confirmed", 0)}, otherAlerts=other,
                           battery={"withBattery": len(with_b), "noBattery": len(nodes) - len(with_b), "low": [{"number": n["number"], "name": n["name"], "mv": n["batteryMv"]} for n in low],
                                    "implausible": [{"number": n["number"], "name": n["name"], "mv": n["batteryMv"]} for n in bad],
                                    "plausibleMv": [cfg.battery_min_plausible_mv, cfg.battery_max_plausible_mv],
                                    "lowLimitMv": cfg.battery_low_mv})
        finally:
            conn.close()

    @app.get("/api/nodes/nearby")
    def nodes_nearby():
        conn = read()
        try:
            cutoff = fmt_ts(utc_now() - timedelta(seconds=90))
            rows = conn.execute("SELECT e.* FROM pairing_events_v2 e WHERE e.kind='nearby' AND e.received_at >= ? ORDER BY e.id DESC", (cutoff,)).fetchall()
            out, seen = [], set()
            for e in rows:
                if e["mac"] in seen or conn.execute("SELECT 1 FROM nodes WHERE mac=? AND state!='removed'", (e["mac"],)).fetchone():
                    continue
                seen.add(e["mac"])
                out.append({"mac": gateway_v2.fmt_mac(e["mac"]), "type": "rain" if e["dev_type"] == DEV_RAIN else "flow", "rssi": e["rssi"], "channel": e["channel"], "lastSeen": e["received_at"]})
            return jsonify(nearby=out)
        finally:
            conn.close()

    @app.get("/api/nodes/<int:ref>/link")
    def node_link(ref):
        """Radio-link history of one node: per hour the share of readings that never arrived (sequence gaps) and the mean RSSI."""
        hours = min(max(request.args.get("hours", 24, type=int), 1), 168)
        conn = read()
        try:
            if conn.execute("SELECT 1 FROM nodes WHERE id=?", (ref,)).fetchone() is None:
                return jsonify(error="Node not found"), 404
            now = utc_now()
            since = fmt_ts(now - timedelta(hours=hours))
            rows = conn.execute("SELECT substr(received_at,1,13) AS h, COALESCE(SUM(gap_before),0) AS missed, COUNT(*) AS got, AVG(rssi) AS rssi "
                                "FROM flow_samples WHERE node_ref=? AND received_at>=? GROUP BY h ORDER BY h", (ref, since)).fetchall()
            pts = [{"hour": r["h"] + ":00:00", "missed": r["missed"], "received": r["got"], "lossPct": round(100.0 * r["missed"] / (r["missed"] + r["got"]), 1) if r["missed"] + r["got"] else 0.0,
                    "rssi": None if r["rssi"] is None else round(r["rssi"], 1)} for r in rows]
            return jsonify(hours=hours, points=pts)
        finally:
            conn.close()

    @app.get("/api/nodes/<int:ref>")
    def node_get(ref):
        conn = read()
        try:
            n = conn.execute("SELECT * FROM nodes WHERE id=?", (ref,)).fetchone()
            if n is None:
                return jsonify(error="Node not found"), 404
            now = utc_now()
            gw = conn.execute("SELECT * FROM gateways WHERE id=?", (n["gateway_ref"],)).fetchone()
            out = node_json(conn, n, gateway_v2.gateway_state(cfg, gw, now)["state"], now, gw["gateway_id"])
            if n["type"] == "flow":
                s = conn.execute("SELECT * FROM flow_samples WHERE node_ref=? ORDER BY measured_at DESC, id DESC LIMIT 1", (ref,)).fetchone()
                out["latest"] = None if s is None else {"flowLpm": s["flow_lpm"], "cumPulses": s["cum_pulses"], "bootId": s["boot_id"], "measuredAt": s["measured_at"],
                                                         "measuredSrc": s["measured_src"], "hwMac": gateway_v2.fmt_mac(s["hw_mac"]) if s["hw_mac"] else None}
            return jsonify(out)
        finally:
            conn.close()

    # -------------------------------------------------------------- commands
    def _gateway_for(conn, gateway_ref):
        if gateway_ref:
            return conn.execute("SELECT * FROM gateways WHERE id=?", (gateway_ref,)).fetchone()
        if conn.execute("SELECT COUNT(*) c FROM gateways").fetchone()["c"] > 1:
            raise Refused("gateway_required", "Several gateways exist: choose which gateway the node belongs to.", 400)
        return gateway_v2.sole_gateway(conn)

    def _respond(cid):
        wait = min(max(request.args.get("wait", 0, type=float), 0), MAX_WAIT_S)
        end = time.time() + wait
        while True:
            c = get_db()
            try:
                row = c.execute("SELECT id, op, status, result, rc, number FROM gateway_commands WHERE id=?", (cid,)).fetchone()
            finally:
                c.close()
            if row["status"] != "sent" or time.time() >= end:
                break
            time.sleep(0.2)
        body = {"commandId": cid, "status": row["status"], "result": row["result"], "number": row["number"]}
        return jsonify(body), (200 if row["status"] == "ok" else 202 if row["status"] == "sent" else 409)

    def _run(fn):
        try:
            return fn()
        except Refused as r:
            return jsonify(error=r.message, code=r.code), r.http
        except CommandError as e:
            return jsonify(error=f"Invalid value: {e}", code=str(e)), 400

    @app.get("/api/commands/<int:cid>")
    def command_get(cid):
        conn = read()
        try:
            with db_session() as w:
                gateway_v2.expire_commands(w, cfg, utc_now())
            row = conn.execute("SELECT id, op, dev_type, number, mac, status, result, rc, created_at, resolved_at FROM gateway_commands WHERE id=?", (cid,)).fetchone()
            if row is None:
                return jsonify(error="Command not found"), 404
            return jsonify({k: row[k] for k in row.keys()})
        finally:
            conn.close()

    @app.post("/api/nodes")
    @auth.require_role("admin")
    def node_add():
        d = request.get_json(silent=True) or {}

        def go():
            t = _dev_type(d.get("type", "flow"))
            mac, code = normalize_mac(d.get("mac")), normalize_code(d.get("code"))
            conn = read()
            try:
                gw = _gateway_for(conn, d.get("gatewayRef"))
                if conn.execute("SELECT 1 FROM nodes WHERE mac=? AND state!='removed'", (mac,)).fetchone():
                    raise Refused("duplicate_mac", "This node (MAC) is already registered.", 409)
            finally:
                conn.close()
            cid = send_command(cfg, gw, "A", t, 0, mac=mac, code=code, name=(d.get("name") or None), user=g.user["email"])
            return _respond(cid)
        return _run(go)

    def _node(conn, ref):
        n = conn.execute("SELECT * FROM nodes WHERE id=? AND state!='removed'", (ref,)).fetchone()
        if n is None:
            raise Refused("not_found", "Node not found.", 404)
        return n, conn.execute("SELECT * FROM gateways WHERE id=?", (n["gateway_ref"],)).fetchone()

    @app.put("/api/nodes/<int:ref>/service")
    @auth.require_role("admin")
    def node_service(ref):
        """Maintenance toggle (backend only, nothing is sent to the gateway): {"outOfService": true|false}."""
        d = request.get_json(silent=True) or {}
        if not isinstance(d.get("outOfService"), bool):
            return jsonify(error="bad_request", message="outOfService must be true or false"), 400

        def go():
            with db_session() as conn:
                n = conn.execute("SELECT * FROM nodes WHERE id=? AND state!='removed'", (ref,)).fetchone()
                if n is None:
                    raise Refused("not_found", "Node not found.", 404)
                conn.execute("UPDATE nodes SET out_of_service=? WHERE id=?", (1 if d["outOfService"] else 0, ref))
                auth.audit(conn, "node_out_of_service" if d["outOfService"] else "node_in_service", f"type={n['type']} number={n['number']}", user=g.user)
                import services
                services.evaluate_alerts(conn, cfg)
            return jsonify(ok=True, outOfService=d["outOfService"])
        return _run(go)

    @app.post("/api/nodes/<int:ref>/replace")
    @auth.require_role("admin")
    def node_replace(ref):
        d = request.get_json(silent=True) or {}

        def go():
            conn = read()
            try:
                n, gw = _node(conn, ref)
                mac, code = normalize_mac(d.get("mac")), normalize_code(d.get("code"))
                if conn.execute("SELECT 1 FROM nodes WHERE mac=? AND state!='removed' AND id!=?", (mac, ref)).fetchone():
                    raise Refused("duplicate_mac", "That MAC is already registered as another node.", 409)
            finally:
                conn.close()
            cid = send_command(cfg, gw, "P", _dev_type(n["type"]), n["number"], mac=mac, code=code, user=g.user["email"])
            return _respond(cid)
        return _run(go)

    @app.patch("/api/nodes/<int:ref>")
    @auth.require_role("admin")
    def node_update(ref):
        d = request.get_json(silent=True) or {}

        def go():
            conn = read()
            try:
                n, gw = _node(conn, ref)
            finally:
                conn.close()
            t = _dev_type(n["type"])
            ids = []
            if "name" in d:
                nm = str(d["name"]).strip()
                if not nm:
                    raise CommandError("bad_name")
                ids.append(send_command(cfg, gw, "N", t, n["number"], name=nm, user=g.user["email"]))
            if "pulsesPerLiter" in d or "txReduce" in d or "txPowerDbm" in d:
                if n["type"] != "flow":
                    raise CommandError("bad_type")
                ppl = d.get("pulsesPerLiter")
                ppl_x10 = int(round(float(ppl) * 10)) if ppl not in (None, "") else (n["ppl_x10"] or 4500)
                if not 100 <= ppl_x10 <= 60000:
                    raise CommandError("bad_calibration")
                if "txPowerDbm" in d:
                    try:
                        x10 = int(round(float(d["txPowerDbm"]) * 10))
                    except (TypeError, ValueError):
                        raise CommandError("bad_tx_power")
                    if x10 not in gateway_v2.TX_BITS:
                        raise CommandError("bad_tx_power")
                    flags = gateway_v2.TX_BITS[x10]
                elif "txReduce" in d:
                    flags = gateway_v2.TX_BITS[85] if d["txReduce"] else 0x00        # legacy switch: on = 8.5 dBm
                else:
                    flags = gateway_v2.TX_BITS.get(n["tx_dbm_x10"] or 85, 0x03)       # a calibration change keeps the node's level
                cid = send_command(cfg, gw, "C", t, n["number"], ppl_x10=ppl_x10, flags=flags, user=g.user["email"])
                ids.append(cid)
                if "pulsesPerLiter" in d:                                    # manual fine-tune: keep the old -> new value in the calibration history
                    import calibration_api
                    with db_session() as hc:
                        calibration_api.record_history(hc, ref, g.user["email"], n["ppl_x10"] or 4500, ppl_x10, "manual", cid)
            if not ids:
                raise CommandError("nothing_to_change")
            return _respond(ids[-1])
        return _run(go)

    @app.delete("/api/nodes/<int:ref>")
    @auth.require_role("admin")
    def node_remove(ref):
        def go():
            conn = read()
            try:
                n, gw = _node(conn, ref)
            finally:
                conn.close()
            return _respond(send_command(cfg, gw, "R", _dev_type(n["type"]), n["number"], user=g.user["email"]))
        return _run(go)
