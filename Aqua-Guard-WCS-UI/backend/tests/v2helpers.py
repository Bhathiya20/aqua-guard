"""Protocol v2 test helpers: a deterministic node simulator that produces the exact JSON the gateway publishes (schema 3)."""
from datetime import datetime, timezone

import gateway_v2
from database import db_session

T0 = 1_767_268_800                       # 2026-01-01 12:00:00 UTC
PREFIX = "waterleak/project001"


def dt(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def fmt_mac(m: str) -> str:
    return gateway_v2.fmt_mac(m)


class NodeSim:
    """One flow node.  `msgs(t0, t1, rate)` yields (measured_time, message) every 2 s; delivery decides the arrival time / recovered flag."""

    def __init__(self, number, mac="447b30854b88", boot=1000, t_boot=None, ppl=450.0, step=2):
        self.number, self.mac, self.boot, self.ppl, self.step = number, mac, boot, ppl, step
        self.t_boot = T0 - 10 if t_boot is None else t_boot
        self.cum = 0.0

    def msgs(self, t0, t1, rate):
        out, t = [], t0
        while t < t1:
            r = rate(t) if callable(rate) else rate
            self.cum += r * self.step / 60.0 * self.ppl
            up = t - self.t_boot
            seq = up // self.step + 1
            pulses = int(self.cum)
            out.append((t, {"schema": 3, "node": self.number, "type": "flow", "mac": fmt_mac(self.mac), "boot": self.boot, "seq": seq, "recovered": False, "backlog": False,
                            "flowRate": round(r, 3), "totalLiters": round(self.cum / self.ppl, 3), "intervalLiters": round(r * self.step / 60.0, 3),
                            "intervalPulses": int(r * self.step / 60.0 * self.ppl), "cumPulses": pulses, "pulsesPerLiter": self.ppl, "intervalMs": self.step * 1000,
                            "batteryMv": 7400, "uptime": up, "status": 0, "channel": 9, "rssi": -55}))
            t += self.step
        return out


def deliver(msg, arrive, gid="gw-test", rx_epoch=True, recovered=False, backlog=False, topic_kind="node"):
    """Hand one message to the backend as if it arrived at time `arrive` (epoch seconds).  rx_epoch=False = gateway clock not synced."""
    m = dict(msg)
    m["rxEpoch"] = int(arrive) if rx_epoch else 0
    m["recovered"], m["backlog"] = recovered, backlog
    with db_session() as conn:
        return gateway_v2.ingest_telemetry(conn, m, dt(arrive), gid=gid, topic_type="flow", topic_number=m["node"])


def feed(sim, t0, t1, rate, skip=(), late=None, gid="gw-test", **kw):
    """Deliver live samples (arrival = measurement time).  `skip` = [(a, b)] measurement windows that are NOT delivered live;
    `late` = (arrival_time) delivers those skipped samples afterwards, flagged recovered, in sequence order."""
    held = []
    for t, m in sim.msgs(t0, t1, rate):
        if any(a <= t < b for a, b in skip):
            held.append((t, m))
            continue
        deliver(m, t, gid=gid, **kw)
    if late is not None:
        for _, m in held:
            deliver(m, late, gid=gid, recovered=True, **kw)
    return held


def registry(nodes, gid="gw-test", rev=1, now=None):
    """nodes = [(type, number, mac12, name, state)]"""
    d = {"schema": 3, "gateway": gid, "rev": rev, "epoch": 0, "nodes": [
        {"number": n, "type": t, "name": nm, "mac": fmt_mac(mac), "state": st, "online": True, "lastSeenEpoch": 0, "ageS": 1, "batteryMv": 7400 if t == "flow" else None,
         "rssi": -50, "pplX10": 4500 if t == "flow" else 0, "txReduce": False, "missing": 0, "addedEpoch": 1700000000} for (t, n, mac, nm, st) in nodes]}
    with db_session() as conn:
        return gateway_v2.handle_registry(conn, gid, d, now or dt(T0))


def info(gid="gw-test", now=None, **kw):
    d = {"schema": 3, "gateway": gid, "fw": "2.0", "proto": 2, "mode": "run", "setupDone": True, "ip": "192.168.1.50", "channel": 9, "rssi": -55, "timeSynced": True,
         "epoch": T0, "tls": False, "tlsVerified": False, "remoteAdd": False, "uptime": 100}
    d.update(kw)
    with db_session() as conn:
        return gateway_v2.handle_info(conn, gid, d, now or dt(T0))


def heartbeat(gid="gw-test", now=None, epoch=None, **kw):
    d = {"schema": 3, "gateway": gid, "uptime": 100, "epoch": T0 if epoch is None else epoch, "wifi": {"up": True, "rssi": -55, "channel": 9, "reconnects": 0, "channelChanges": 0},
         "mqtt": {"up": True, "reconnects": 1, "published": 10, "publishFail": 0}, "queue": {"depth": 2, "capacity": 256, "overflow": 1}, "freeHeap": 150000,
         "rx": {"frames": 100, "rejected": 3, "badTag": 2, "badValue": 1, "unknownMac": 0, "acks": 90, "nacks": 4, "queueDropped": 5}, "nodes": []}
    d.update(kw)
    with db_session() as conn:
        return gateway_v2.handle_heartbeat(conn, d, now or dt(T0))
