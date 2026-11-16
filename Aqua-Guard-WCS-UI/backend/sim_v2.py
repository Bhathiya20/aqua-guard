"""Protocol-v2 plant simulator: N flow nodes on a given pipeline document, with leaks, silent sensors, radio loss and counter resets.

A *simulation* of the water network and of the gateway's JSON, used to exercise the backend and the analysis without hardware.  It is NOT
evidence that any sensor, radio or gateway works.  The physical model is deliberately simple and exact so that tests can state the truth:

* every outlet draws water in scheduled bursts (zero at night);
* a node on pipe Q measures the demand of all outlets below Q, plus every leak located below it
  (a leak on the same pipe is seen only by nodes upstream of the leak position);
* a leak is {"part": id, "lpm": L/min, "start": s, "end": s|None, "pos": 0..1}; times are seconds from the start of the run.
"""
import json
import random
from dataclasses import dataclass, field
from typing import Iterator

from pipeline.model import parse
from pipeline.validate import build_tree

GATEWAY = "gw-sim"
USE_WINDOWS = [(7 * 3600, 9 * 3600, 5.0), (12 * 3600, 13 * 3600, 3.0), (18 * 3600, 21 * 3600, 6.0)]     # (from, to, L/min) seconds of the local day
PPL = 450.0


def sim_mac(number: int) -> str:
    return f"02:51:4D:00:00:{number:02X}"


@dataclass
class Faults:
    silent: dict = field(default_factory=dict)       # node number -> [(from_s, to_s)] nothing is delivered live
    late: dict = field(default_factory=dict)         # node number -> seconds after the end of a silent window when it is retransmitted (flagged recovered); absent = lost for good
    resets: dict = field(default_factory=dict)       # node number -> [at_s]  the node reboots: new boot id, pulse counter and uptime restart
    loss: float = 0.0                                # random packet loss 0..1 (lost for good)
    unsynced: bool = False                           # gateway clock not synchronised: rxEpoch = 0


class Plant:
    def __init__(self, doc: dict, leaks=None, tz_min: int = 0, windows=None):
        self.net = parse(doc)
        self.tree = build_tree(self.net)
        self.leaks = [dict(pos=0.9, end=None, start=0) | lk for lk in (leaks or [])]
        self.tz_min = tz_min
        self.windows = windows if windows is not None else USE_WINDOWS
        self.outlets = [p for p in self.net.order if p in self.net.parts and self.net.parts[p].kind == "outlet"]
        self.nodes = {n.raw["number"]: n for n in self.net.nodes.values() if n.raw.get("nodeType", "flow") == "flow"}

    def _below(self, pid):
        out, stack = [], [pid]
        while stack:
            cur = stack.pop()
            out.append(cur)
            stack.extend(self.tree.children.get(cur, []))
        return out

    def _above(self, pid):
        out = []
        while pid in self.tree.parent:
            pid = self.tree.parent[pid]
            out.append(pid)
        return out

    def demand(self, outlet_idx: int, t: float, t0_epoch: int) -> float:
        sod = (t0_epoch + t + self.tz_min * 60) % 86400
        for a, b, rate in self.windows:
            if a <= sod < b and (sod - a + outlet_idx * 150) % 600 < 300:
                return rate
        return 0.0

    def flow(self, number: int, t: float, t0_epoch: int) -> float:
        """True flow (L/min) through the node `number` at run time t."""
        n = self.nodes[number]
        pipe, pos = n.raw["pipe"], float(n.raw.get("pos", 0.5))
        below = set(self._below(pipe))
        total = sum(self.demand(i, t, t0_epoch) for i, o in enumerate(self.outlets) if o in below)
        for lk in self.leaks:
            if lk["start"] <= t and (lk["end"] is None or t < lk["end"]):
                if lk["part"] == pipe:
                    seen = pos < lk["pos"]
                else:
                    seen = pipe in self._above(lk["part"])
                if seen:
                    total += lk["lpm"]
        return total


def registry_json(plant: Plant, rev: int = 1, epoch: int = 0) -> dict:
    return {"schema": 3, "gateway": GATEWAY, "rev": rev, "epoch": epoch, "nodes": [
        {"number": num, "type": "flow", "name": plant.nodes[num].label or f"Node {num}", "mac": sim_mac(num), "state": "paired", "online": True, "lastSeenEpoch": epoch,
         "ageS": 1, "batteryMv": 7400, "rssi": -55, "pplX10": int(PPL * 10), "txReduce": False, "missing": 0, "addedEpoch": 1700000000} for num in sorted(plant.nodes)]}


def info_json(epoch: int, synced: bool = True, uptime: int = 100) -> dict:
    return {"schema": 3, "gateway": GATEWAY, "fw": "sim", "proto": 2, "mode": "run", "setupDone": True, "ip": "127.0.0.1", "channel": 9, "rssi": -55,
            "timeSynced": synced, "epoch": epoch if synced else 0, "tls": False, "tlsVerified": False, "remoteAdd": False, "uptime": uptime}


def heartbeat_json(epoch: int, synced: bool = True, uptime: int = 100) -> dict:
    return {"schema": 3, "gateway": GATEWAY, "uptime": uptime, "epoch": epoch if synced else 0, "wifi": {"up": True, "rssi": -55, "channel": 9, "reconnects": 0, "channelChanges": 0},
            "mqtt": {"up": True, "reconnects": 1, "published": 0, "publishFail": 0}, "queue": {"depth": 0, "capacity": 256, "overflow": 0}, "freeHeap": 150000,
            "rx": {"frames": 0, "rejected": 0, "badTag": 0, "badValue": 0, "unknownMac": 0, "acks": 0, "nacks": 0, "queueDropped": 0}, "nodes": []}


def generate(plant: Plant, t0_epoch: int, seconds: int, faults: Faults | None = None, step: int = 2, seed: int = 1) -> Iterator[tuple]:
    """Yield (arrival_run_time_s, node_number, message) in ARRIVAL order.  The message carries the exact JSON the gateway publishes (schema 3)."""
    f = faults or Faults()
    rnd = random.Random(seed)
    out = []
    for num in sorted(plant.nodes):
        boots = [(0, 1000 + num)] + [(at, 2000 + num + 10 * i) for i, at in enumerate(sorted(f.resets.get(num, [])), 1)]
        cum, boot_at, boot_id = 0.0, boots[0][0], boots[0][1]
        pending = list(boots[1:])
        seq_n = 0
        t = 0
        while t < seconds:
            if pending and t >= pending[0][0]:
                boot_at, boot_id = pending.pop(0)
                cum, seq_n = 0.0, 0
            lpm = plant.flow(num, t, t0_epoch)
            cum += lpm * step / 60.0 * PPL
            seq_n += 1
            up = t - boot_at
            msg = {"schema": 3, "node": num, "type": "flow", "mac": sim_mac(num), "boot": boot_id, "seq": seq_n, "recovered": False, "backlog": False,
                   "flowRate": round(lpm, 3), "totalLiters": round(cum / PPL, 3), "intervalLiters": round(lpm * step / 60.0, 3),
                   "intervalPulses": int(lpm * step / 60.0 * PPL), "cumPulses": int(cum), "pulsesPerLiter": PPL, "intervalMs": step * 1000,
                   "batteryMv": 7400, "uptime": up, "status": 0, "channel": 9, "rssi": -55}
            arrival, recovered = t, False
            win = next((w for w in f.silent.get(num, []) if w[0] <= t < w[1]), None)
            if win is not None:
                if num in f.late:
                    arrival, recovered = win[1] + f.late[num], True
                else:
                    t += step
                    continue
            elif f.loss and rnd.random() < f.loss:
                t += step
                continue
            msg["recovered"] = recovered
            out.append((arrival, num, msg))
            t += step
    out.sort(key=lambda x: (x[0], x[1], x[2]["seq"]))
    return iter(out)


def deliver_all(cfg, plant: Plant, t0_epoch: int, seconds: int, faults: Faults | None = None, step: int = 2, publish=None):
    """Feed a whole scenario into the backend at VIRTUAL time (no sleeping).  `publish(sub, obj, epoch)` defaults to the in-process MQTT handler."""
    import mqtt_service
    from datetime import datetime, timezone
    f = faults or Faults()

    def pub(sub, obj, epoch):
        if publish:
            return publish(sub, obj, epoch)
        return mqtt_service.handle_message(cfg, f"{cfg.mqtt_topic_prefix.rstrip('/')}/{sub}", json.dumps(obj).encode(), now=datetime.fromtimestamp(epoch, timezone.utc))

    pub(f"{GATEWAY}/nodes/registry", registry_json(plant, epoch=t0_epoch), t0_epoch)
    pub(f"{GATEWAY}/gateway/info", info_json(t0_epoch, not f.unsynced), t0_epoch)
    next_hb = 0
    n = 0
    for arrival, num, msg in generate(plant, t0_epoch, seconds, f, step):
        if arrival >= next_hb:
            pub("gateway/heartbeat", heartbeat_json(int(t0_epoch + arrival), not f.unsynced), int(t0_epoch + arrival))
            next_hb = arrival + 30
        msg = dict(msg, rxEpoch=0 if f.unsynced else int(t0_epoch + arrival))
        pub(f"{GATEWAY}/node/{num}", msg, int(t0_epoch + arrival))
        n += 1
    pub("gateway/heartbeat", heartbeat_json(t0_epoch + seconds, not f.unsynced), t0_epoch + seconds)
    return n
