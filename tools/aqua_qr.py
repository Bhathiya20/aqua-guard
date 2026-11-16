"""Aqua Guard node QR payloads - Python twin of libraries/AquaProtocol/src/aqua_join.h.

Two accepted forms (v1):
  URL      http://192.168.4.1/add?v=1&mac=AA:BB:CC:DD:EE:FF&code=XXXXXXXXXXXX&type=flow
  compact  AQG1;AABBCCDDEEFF;XXXXXXXXXXXX;flow

The same rules are implemented in the firmware (aquaQrParse), the gateway portal JS and the web app;
tests/qr_vectors.json holds the shared cases that every implementation must agree on.
"""
import re
from urllib.parse import parse_qs, unquote, urlsplit

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"        # Crockford base32 (no I L O U)
CODE_LEN = 12
TYPES = {"flow": 1, "rain": 2}


class QrError(ValueError):
    def __init__(self, kind, message=""):
        super().__init__(f"{kind}: {message}" if message else kind)
        self.kind = kind                              # format | version | mac | code | type


def normalize_code(text: str) -> str:
    """Ignore dashes/spaces/case; O->0, I/L->1.  Raises QrError('code')."""
    out = []
    for ch in text:
        if ch in "- _":
            continue
        ch = ch.upper()
        ch = {"O": "0", "I": "1", "L": "1"}.get(ch, ch)
        if ch not in ALPHABET:
            raise QrError("code", f"invalid symbol {ch!r}")
        out.append(ch)
    if len(out) != CODE_LEN:
        raise QrError("code", f"expected {CODE_LEN} symbols, got {len(out)}")
    return "".join(out)


def format_code(code12: str) -> str:
    return "-".join(code12[i:i + 4] for i in range(0, CODE_LEN, 4))


def normalize_mac(text: str) -> str:
    h = re.sub(r"[:\- ]", "", text).upper()
    if not re.fullmatch(r"[0-9A-F]{12}", h):
        raise QrError("mac", f"bad MAC {text!r}")
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


def parse(text) -> dict:
    """Returns {'mac': 'AA:BB:..', 'code': '12 symbols', 'type': 'flow'|'rain'}.  Error precedence matches the firmware:
    format -> version -> mac -> code -> type."""
    t = (text or "").strip()
    if not t or len(t) >= 200:
        raise QrError("format")
    if t[:3].upper() == "AQG":
        f = t.split(";")
        if len(f) != 4:
            raise QrError("format", "compact form needs 4 fields")
        if f[0].upper() != "AQG1":
            raise QrError("version")
        mac, code, typ = f[1], f[2], f[3]
    else:
        if "?" not in t or "/add" not in t:
            raise QrError("format")
        q = parse_qs(urlsplit(t).query, keep_blank_values=True)
        get = lambda k: unquote(q[k][0]) if k in q else None
        ver, mac, code, typ = get("v"), get("mac"), get("code"), get("type")
        if None in (ver, mac, code, typ):
            raise QrError("format", "missing field")
        if ver != "1":
            raise QrError("version")
    nmac = normalize_mac(mac)
    ncode = normalize_code(code)
    if typ.lower() not in TYPES:
        raise QrError("type")
    return {"mac": nmac, "code": ncode, "type": typ.lower()}


def build(mac: str, code: str, typ: str = "flow", compact: bool = False) -> str:
    mac, code = normalize_mac(mac), normalize_code(code)
    if typ not in TYPES:
        raise QrError("type")
    if compact:
        return f"AQG1;{mac.replace(':', '')};{code};{typ}"
    return f"http://192.168.4.1/add?v=1&mac={mac}&code={code}&type={typ}"
