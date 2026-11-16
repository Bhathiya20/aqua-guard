import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
from app import create_app  # noqa: E402
from config import Settings  # noqa: E402

ADMIN_EMAIL = "admin@test.local"
ADMIN_PW = "Sup3rSecret-Admin"
USER_EMAIL = "user@test.local"
USER_PW = "Reg1sterd-User-pw"
T0 = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def cfg(tmp_path):
    s = Settings()
    s.db_path = tmp_path / "test.db"
    s.secret_key = "test-secret"
    s.mqtt_enabled = False
    s.default_admin_email = ADMIN_EMAIL
    s.default_admin_password = ADMIN_PW
    s.cors_origins = ["http://localhost:5173"]
    return s


@pytest.fixture()
def app(cfg):
    a = create_app(cfg, start_background=False)
    a.testing = True
    return a


@pytest.fixture()
def conn(app, cfg):
    """A transaction-less connection for assertions."""
    c = database.get_db()
    yield c
    c.close()


def flow_msg(node=1, boot=1000, seq=1, cum=450, interval=450, ppl=450.0, lpm=None, interval_ms=2000, epoch=None, **kw):
    m = {"schema": 2, "node": node, "boot": boot, "seq": seq, "recovered": False, "backlog": False,
         "flowRate": lpm if lpm is not None else round(interval / ppl * 60000.0 / interval_ms, 3),
         "totalLiters": round(cum / ppl, 3), "intervalLiters": round(interval / ppl, 3),
         "intervalPulses": interval, "cumPulses": cum, "pulsesPerLiter": ppl, "intervalMs": interval_ms,
         "uptime": seq * 2, "status": 0, "channel": 6, "rssi": -50}
    if epoch is not None:
        m["rxEpoch"] = int(epoch)
    m.update(kw)
    return m


def rain_msg(boot=5000, seq=1, rain=False, level=40.0, level_valid=True, turb=3500, clear=True, full=False, valve=False, **kw):
    m = {"schema": 2, "node": 3, "boot": boot, "seq": seq, "recovered": False, "backlog": False, "rain": rain,
         "waterLevel": level if level_valid else None, "levelValid": level_valid, "distance": 12.5 if level_valid else None,
         "turbidity": turb, "turbidityValid": True, "waterClear": clear, "tankFull": full, "valveOpen": valve,
         "valveState": 0, "valveReason": 0, "buzzer": False, "firstFlush": False, "dryRun": True, "faults": 0,
         "rainEvents": 0, "uptime": seq * 2, "channel": 6, "rssi": -55}
    m.update(kw)
    return m


def gw_heartbeat(gateway="gw-test", nodes=None, **kw):
    m = {"schema": 2, "gateway": gateway, "uptime": 100, "epoch": 0, "wifi": {"up": True, "rssi": -60, "channel": 6, "reconnects": 0, "channelChanges": 0},
         "mqtt": {"up": True, "reconnects": 1, "published": 10, "publishFail": 0}, "queue": {"depth": 0, "capacity": 256, "overflow": 0},
         "freeHeap": 150000, "rx": {"frames": 10, "rejected": 0, "badTag": 0, "badValue": 0, "unknownMac": 0, "acks": 10, "nacks": 0},
         "nodes": nodes if nodes is not None else []}
    m.update(kw)
    return m


def ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def login(client, email=ADMIN_EMAIL, password=ADMIN_PW):
    r = client.post("/api/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["csrfToken"]
