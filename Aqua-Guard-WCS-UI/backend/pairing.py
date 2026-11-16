"""Device registry + secure pairing (backend side).  See docs/PAIRING.md.

Trust model
-----------
* A device is identified by its application `uid` (never by MAC address).
* Each device holds a 16-byte secret  S = KDF(GMK,'S',uid,0)  flashed at provisioning time.  S is never
  transmitted.  The backend can re-derive S from the site master key (GMK, AQUAGUARD_GMK).
* The owner proves physical possession of the device with a one-time *pairing code* (printed / QR).
  The code is stored only as a salted hash, attempts are rate limited and the device locks after N failures.
* The device proves knowledge of S by answering a gateway challenge (SipHash MAC).  The gateway verifies it
  AND the backend independently re-verifies the transcript carried in the signed pairing event.
* backend<->gateway messages are authenticated with Kcmd = KDF(GMK,'C') and carry timestamps (replay window).
"""
import re
import secrets
import struct
import threading
import time
from datetime import timedelta

from werkzeug.security import check_password_hash, generate_password_hash

import ingest
from ingest import fmt_ts, utc_now

M64 = (1 << 64) - 1
FIRST_DYNAMIC_SLOT, MAX_SLOTS = 4, 12
FIRST_DYNAMIC_UID = 100
DEV_TYPES = {1: "flow", 2: "rain"}
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"        # no 0/O/1/I/L: easy to read from a label
CMD_WINDOW_S = 300


# ------------------------------------------------------------------ SipHash-2-4 + KDF (must match libraries/AquaProtocol)
def _rotl(x, b):
    return ((x << b) | (x >> (64 - b))) & M64


def siphash24(key: bytes, data: bytes) -> int:
    k0, k1 = struct.unpack("<QQ", key)
    v0, v1 = 0x736f6d6570736575 ^ k0, 0x646f72616e646f6d ^ k1
    v2, v3 = 0x6c7967656e657261 ^ k0, 0x7465646279746573 ^ k1

    def rnd():
        nonlocal v0, v1, v2, v3
        v0 = (v0 + v1) & M64; v1 = _rotl(v1, 13); v1 ^= v0; v0 = _rotl(v0, 32)
        v2 = (v2 + v3) & M64; v3 = _rotl(v3, 16); v3 ^= v2
        v0 = (v0 + v3) & M64; v3 = _rotl(v3, 21); v3 ^= v0
        v2 = (v2 + v1) & M64; v1 = _rotl(v1, 17); v1 ^= v2; v2 = _rotl(v2, 32)

    n = len(data)
    for i in range(0, n - n % 8, 8):
        m, = struct.unpack_from("<Q", data, i)
        v3 ^= m; rnd(); rnd(); v0 ^= m
    b = (n << 56) & M64
    for i, byte in enumerate(data[n - n % 8:]):
        b |= byte << (8 * i)
    v3 ^= b; rnd(); rnd(); v0 ^= b
    v2 ^= 0xFF
    rnd(); rnd(); rnd(); rnd()
    return v0 ^ v1 ^ v2 ^ v3


def kdf(key: bytes, label: str, a: int, b: int) -> bytes:
    base = bytes([ord(label)]) + struct.pack("<II", a & 0xFFFFFFFF, b & 0xFFFFFFFF)
    return struct.pack("<Q", siphash24(key, base + b"\x00")) + struct.pack("<Q", siphash24(key, base + b"\x01"))


def pair_mac(secret: bytes, label: str, uid: int, boot: int, chal: bytes, extra: int) -> bytes:
    msg = bytes([ord(label)]) + struct.pack("<II", uid, boot) + chal + bytes([extra & 0xFF])
    return struct.pack("<Q", siphash24(secret, msg))


def cmd_key(gmk: bytes) -> bytes:
    return kdf(gmk, "C", 0, 0)


def device_secret(gmk: bytes, uid: int) -> bytes:
    return kdf(gmk, "S", uid, 0)


def text_tag(ckey: bytes, body: str) -> str:
    return struct.pack("<Q", siphash24(ckey, body.encode())).hex()


def parse_gmk(hexstr: str) -> bytes | None:
    if not re.fullmatch(r"[0-9a-fA-F]{32}", hexstr or ""):
        return None
    k = bytes.fromhex(hexstr)
    return None if k == bytes(16) else k


# ------------------------------------------------------------------ command channel (backend -> gateway)
_ts_lock = threading.Lock()
_last_cmd_ts = 0
publisher = None            # callable(text) -> bool ; set by mqtt_service (tests replace it)
sent_commands: list[str] = []   # last few commands (diagnostics / tests)


def build_cmd(gmk: bytes, op: str, uid: int, slot: int, dev_type: int, chal: bytes | None, now_epoch: int | None = None) -> str:
    global _last_cmd_ts
    with _ts_lock:
        ts = max(int(now_epoch if now_epoch is not None else time.time()), _last_cmd_ts + 1)
        _last_cmd_ts = ts
    body = f"AQC1 {op} {uid} {slot} {dev_type} {chal.hex() if chal else '-'} {ts}"
    return f"{body} {text_tag(cmd_key(gmk), body)}"


def send_cmd(text: str) -> bool:
    sent_commands.append(text)
    del sent_commands[:-20]
    return bool(publisher and publisher(text))


# ------------------------------------------------------------------ helpers
class PairingError(Exception):
    def __init__(self, message, status=400, code=None):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


def _gmk(cfg) -> bytes:
    g = parse_gmk(cfg.gmk_hex)
    if g is None:
        raise PairingError("Pairing is not configured: set AQUAGUARD_GMK (32 hex chars) in backend/.env", 503, "NOT_CONFIGURED")
    return g


def pairing_configured(cfg) -> bool:
    return parse_gmk(cfg.gmk_hex) is not None


def normalise_code(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(code or "").upper())


def new_pairing_code() -> str:
    c = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    return f"{c[:4]}-{c[4:]}"


def code_hash(code: str) -> str:
    return generate_password_hash(normalise_code(code), method="pbkdf2:sha256:60000")


def attempt(conn, uid, kind, ok, mac=None, detail="", email=None, ip=None):
    conn.execute("INSERT INTO pairing_attempts (uid,mac,kind,ok,detail,user_email,ip) VALUES (?,?,?,?,?,?,?)",
                 (uid, mac, kind, int(bool(ok)), str(detail)[:300], email, ip))
    conn.execute("DELETE FROM pairing_attempts WHERE ts < ?", (fmt_ts(utc_now() - timedelta(days=90)),))


def format_device_id(dev_type: str, uid: int) -> str:
    return f"AG-{'FLOW' if dev_type == 'flow' else 'RAIN'}-{uid:03d}"


# ------------------------------------------------------------------ provisioning (tools/provision_device.py)
def provision_device(conn, gmk: bytes, name: str, dev_type: str = "flow", uid: int | None = None):
    """Create a registry row in state 'provisioned'.  Returns (row dict, pairing_code, secret_bytes).
    The pairing code is returned ONCE; only its hash is stored."""
    name = (name or "").strip()[:60]
    if uid is None:
        r = conn.execute("SELECT COALESCE(MAX(uid),0) m FROM device_registry").fetchone()
        uid = max(FIRST_DYNAMIC_UID, r["m"] + 1)
    if uid < FIRST_DYNAMIC_UID or uid > 0x7FFFFFFF:
        raise PairingError(f"uid must be >= {FIRST_DYNAMIC_UID} (1..99 are reserved for legacy nodes)")
    if conn.execute("SELECT 1 FROM device_registry WHERE uid=?", (uid,)).fetchone():
        raise PairingError(f"device uid {uid} is already registered", 409, "DUPLICATE")
    code = new_pairing_code()
    device_id = format_device_id(dev_type, uid)
    conn.execute("INSERT INTO device_registry (uid,device_id,device_type,display_name,pairing_status,code_hash) VALUES (?,?,?,?, 'provisioned', ?)",
                 (uid, device_id, dev_type, name or device_id, code_hash(code)))
    return {"uid": uid, "deviceId": device_id}, code, device_secret(gmk, uid)


# ------------------------------------------------------------------ discovery (gateway events -> backend)
EVENT_RE = re.compile(r"^AQE1 ([DOF]) (\d+) ([0-9a-f]{12}) (\d+) (\d+) (\d+) (-?\d+) ([0-9a-f]{8}) (\d+) (-|[0-9a-f]{16}) (-|[0-9a-f]{16}) (\d+) ([0-9a-f]{16})$")


def _mac_str(h):
    return ":".join(h[i:i + 2] for i in range(0, 12, 2)).upper()


def handle_pair_event(conn, cfg, text: str, now=None) -> dict:
    """Process one signed gateway pairing event.  Returns {'result': ..., 'commands': [text,...]} - the caller
    publishes the commands AFTER the transaction commits.  Never raises on hostile input."""
    now = now or utc_now()
    out = {"result": "rejected", "commands": []}
    gmk = parse_gmk(cfg.gmk_hex)
    if gmk is None:
        out["result"] = "not_configured"
        return out
    m = EVENT_RE.match(text.strip())
    if not m:
        out["reason"] = "format"
        return out
    body, tag = text.strip().rsplit(" ", 1)
    import hmac
    if not hmac.compare_digest(text_tag(cmd_key(gmk), body), tag):
        attempt(conn, None, "event_bad_tag", False, detail="pairing event with invalid tag (not from our gateway?)")
        out["reason"] = "tag"
        return out
    kind, uid, mac12, dtype, fw, channel, rssi, boot_hex, slot, chal_hex, resp_hex, epoch, _ = m.groups()
    uid, dtype, fw, channel, rssi, slot, epoch = int(uid), int(dtype), int(fw), int(channel), int(rssi), int(slot), int(epoch)
    if abs(epoch - int(now.timestamp())) > CMD_WINDOW_S:
        out["reason"] = "stale"
        return out
    boot = struct.unpack("<I", bytes.fromhex(boot_hex))[0]
    mac = _mac_str(mac12)
    typ = DEV_TYPES.get(dtype, "flow")
    fw_s = f"{fw >> 8}.{fw & 0xFF}"
    ts = fmt_ts(now)
    reg = conn.execute("SELECT * FROM device_registry WHERE uid=?", (uid,)).fetchone()

    if kind == "D":                      # an unauthorised device is announcing itself
        conn.execute(
            "INSERT INTO discovered_devices (uid,mac,device_type,firmware_version,channel,rssi,first_seen,last_seen) VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(uid,mac) DO UPDATE SET device_type=excluded.device_type, firmware_version=excluded.firmware_version,"
            " channel=excluded.channel, rssi=excluded.rssi, last_seen=excluded.last_seen, announces=announces+1",
            (uid, mac, typ, fw_s, channel, rssi, ts, ts))
        conn.execute("DELETE FROM discovered_devices WHERE last_seen < ?", (fmt_ts(now - timedelta(hours=24)),))
        out["result"] = "discovered"
        if reg is not None and reg["legacy"] == 0 and reg["pairing_status"] in ("paired", "pairing") and reg["slot"]:
            # The gateway lost its authorisation (reboot) or the device rebooted: re-authorise automatically.
            # Only for devices the owner already approved - a new/unpaired device is NEVER auto-authorised.
            last = reg["pairing_started"] or 0
            chal = bytes.fromhex(reg["challenge"]) if (reg["pairing_status"] == "pairing" and reg["challenge"]) else None
            if reg["pairing_status"] == "pairing" and time.time() - last > cfg.pairing_timeout_s:
                conn.execute("UPDATE device_registry SET pairing_status='provisioned', slot=NULL, challenge=NULL, pairing_error='timed out' WHERE uid=?", (uid,))
            else:
                out["commands"].append(build_cmd(gmk, "auth", uid, reg["slot"], dtype, chal))
                out["result"] = "reauthorised"
        return out

    # kind O / F: challenge-response transcript from the gateway
    if reg is None or reg["legacy"] or reg["pairing_status"] not in ("pairing", "paired"):
        attempt(conn, uid, "challenge_unexpected", False, mac, "pairing result for a device that is not pairing")
        out["reason"] = "unexpected"
        return out
    if kind == "F":
        attempt(conn, uid, "challenge_fail", False, mac, "gateway: device failed the challenge-response (wrong secret / clone?)")
        if reg["pairing_status"] == "pairing":
            conn.execute("UPDATE device_registry SET pairing_error=? WHERE uid=?", ("Device failed the secure challenge (wrong secret). Is this the right device?", uid))
        out["result"] = "challenge_failed"
        return out
    # kind O: independently verify the proof
    if chal_hex == "-" or resp_hex == "-" or slot != reg["slot"]:
        out["reason"] = "incomplete"
        return out
    chal, resp = bytes.fromhex(chal_hex), bytes.fromhex(resp_hex)
    if reg["pairing_status"] == "pairing":
        if not reg["challenge"] or reg["challenge"] != chal_hex:
            attempt(conn, uid, "challenge_mismatch", False, mac, "challenge in event does not match the one issued")
            out["reason"] = "challenge"
            return out
    want = pair_mac(device_secret(gmk, uid), "R", uid, boot, chal, 0)
    if not hmac.compare_digest(want, resp):
        attempt(conn, uid, "challenge_fail", False, mac, "backend verification of the device response failed")
        out["result"] = "challenge_failed"
        return out
    conn.execute(
        "UPDATE device_registry SET pairing_status='paired', mac=?, device_type=?, firmware_version=?, last_boot_id=?, last_verified_at=?,"
        " challenge=NULL, pairing_error=NULL, paired_at=COALESCE(paired_at, ?), code_failures=0, code_locked_until=NULL WHERE uid=?",
        (mac, typ, fw_s, boot, ts, ts, uid))
    conn.execute("DELETE FROM discovered_devices WHERE uid=?", (uid,))
    attempt(conn, uid, "challenge_ok", True, mac, f"device proved possession of its secret (boot {boot_hex}, slot {slot})")
    out["result"] = "paired"
    return out


# ------------------------------------------------------------------ owner actions
def start_pairing(conn, cfg, uid: int, code: str, name: str | None, user_email: str, ip: str = "") -> dict:
    """Verify the pairing code, allocate a slot, return the signed authorise command (publish after commit).
    Failures that must be RECORDED (wrong code / lockout) are returned as {"error": PairingError} so the transaction commits;
    other problems raise and roll back."""
    gmk = _gmk(cfg)
    reg = conn.execute("SELECT * FROM device_registry WHERE uid=?", (uid,)).fetchone()
    if reg is None or reg["legacy"]:
        attempt(conn, uid, "code_unknown", False, detail="unknown / legacy device", email=user_email, ip=ip)
        raise PairingError("This device is not provisioned in this system, so it cannot be paired.", 404, "UNKNOWN_DEVICE")
    now = time.time()
    if reg["code_locked_until"] and reg["code_locked_until"] > now:
        attempt(conn, uid, "code_locked", False, detail="attempt while locked", email=user_email, ip=ip)
        return {"error": PairingError("Too many wrong pairing codes. Pairing for this device is locked for a while.", 429, "LOCKED")}
    if reg["pairing_status"] == "paired":
        raise PairingError("This device is already paired. Unpair it first.", 409, "ALREADY_PAIRED")
    if not reg["code_hash"] or not check_password_hash(reg["code_hash"], normalise_code(code)):
        fails = (reg["code_failures"] or 0) + 1
        locked = now + cfg.pairing_lock_minutes * 60 if fails >= cfg.pairing_code_max_failures else None
        conn.execute("UPDATE device_registry SET code_failures=?, code_locked_until=? WHERE uid=?", (0 if locked else fails, locked, uid))
        attempt(conn, uid, "code_bad", False, detail=f"wrong code ({fails}/{cfg.pairing_code_max_failures})" + (" - LOCKED" if locked else ""),
                email=user_email, ip=ip)
        return {"error": PairingError("Incorrect pairing code." + (" Device locked." if locked else ""), 403, "BAD_CODE")}
    used = {r["slot"] for r in conn.execute("SELECT slot FROM device_registry WHERE slot IS NOT NULL AND uid<>?", (uid,))}
    slot = reg["slot"] if reg["slot"] else next((s for s in range(FIRST_DYNAMIC_SLOT, MAX_SLOTS + 1) if s not in used), None)
    if slot is None:
        raise PairingError(f"All {MAX_SLOTS - FIRST_DYNAMIC_SLOT + 1} wireless slots are in use. Unpair a device first.", 409, "NO_SLOT")
    chal = secrets.token_bytes(8)
    dtype = 2 if reg["device_type"] == "rain" else 1
    new_name = (name or "").strip()[:60] or reg["display_name"]
    conn.execute("UPDATE device_registry SET pairing_status='pairing', slot=?, challenge=?, pairing_started=?, pairing_error=NULL,"
                 " display_name=?, paired_by=?, code_failures=0, code_locked_until=NULL WHERE uid=?",
                 (slot, chal.hex(), now, new_name, user_email, uid))
    attempt(conn, uid, "code_ok", True, detail=f"pairing code accepted, slot {slot} reserved", email=user_email, ip=ip)
    return {"command": build_cmd(gmk, "auth", uid, slot, dtype, chal), "slot": slot}


def unpair(conn, cfg, uid: int, user_email: str) -> dict:
    gmk = _gmk(cfg)
    reg = conn.execute("SELECT * FROM device_registry WHERE uid=?", (uid,)).fetchone()
    if reg is None:
        raise PairingError("Device not found", 404)
    if reg["legacy"]:
        raise PairingError("Built-in nodes 1-3 use fixed radio slots and cannot be unpaired. Rename or recalibrate them instead.", 409, "LEGACY")
    cmd = build_cmd(gmk, "revoke", uid, reg["slot"] or 0, 2 if reg["device_type"] == "rain" else 1, None)
    # a fresh pairing code is required to pair it again: invalidate the old one
    conn.execute("UPDATE device_registry SET pairing_status='unpaired', slot=NULL, challenge=NULL, pairing_error=NULL,"
                 " unpaired_at=?, code_hash=NULL, last_boot_id=NULL WHERE uid=?", (fmt_ts(utc_now()), uid))
    attempt(conn, uid, "unpair", True, detail="unpaired by owner", email=user_email)
    return {"command": cmd}


def reset_code(conn, uid: int, user_email: str):
    """Issue a fresh pairing code for an unpaired / provisioned device (shown once)."""
    reg = conn.execute("SELECT * FROM device_registry WHERE uid=?", (uid,)).fetchone()
    if reg is None or reg["legacy"]:
        raise PairingError("Device not found", 404)
    if reg["pairing_status"] in ("paired", "pairing"):
        raise PairingError("Unpair the device before generating a new pairing code.", 409)
    code = new_pairing_code()
    conn.execute("UPDATE device_registry SET code_hash=?, code_failures=0, code_locked_until=NULL, pairing_status='provisioned' WHERE uid=?",
                 (code_hash(code), uid))
    attempt(conn, uid, "code_reset", True, detail="new pairing code issued", email=user_email)
    return code


def expire_stale_pairings(conn, cfg):
    conn.execute("UPDATE device_registry SET pairing_status='provisioned', slot=NULL, challenge=NULL,"
                 " pairing_error='Pairing timed out - the device did not complete the secure handshake' "
                 "WHERE pairing_status='pairing' AND pairing_started < ?", (time.time() - cfg.pairing_timeout_s,))


# ------------------------------------------------------------------ views
def device_online_state(cfg, dev_row, gw_online, now=None):
    now = now or utc_now()
    if dev_row is None:
        return "offline" if gw_online else "unknown", None, None
    stamps = [x for x in (dev_row["last_seen"], dev_row["gw_last_seen"]) if x]
    freshest = max(stamps, default=None)
    if freshest is None:
        return ("offline" if gw_online else "unknown"), None, None
    age = max(0, int((now - ingest.parse_ts(freshest)).total_seconds()))
    if age <= cfg.node_offline_s:
        return "online", freshest, age
    return ("offline" if gw_online else "unknown"), freshest, age
