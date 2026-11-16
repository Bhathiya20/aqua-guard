"""AQC2: signed + encrypted backend -> gateway command (protocol v2).  Mirrors libraries/AquaProtocol/src/aqua_registry.h.

  AQC2 <ts> <nonce24hex> <op> <type> <number> <mac12|-> <pplX10|-> <flags|-> <namehex|-> <codeEnc24hex|-> <tag16hex>
  op: A add | N rename | R remove | P replace hardware | C calibrate.     type: 1 flow, 2 rain.
  The pairing code is ChaCha20-encrypted (key = KDF(site,'E',0)||KDF(site,'E',1), the 12 nonce bytes as nonce, counter 0).
  tag = SipHash-2-4 (key KDF(site,'C')) over the text before the last field, 8 bytes little-endian as lower-case hex.
The site key is the command key (AQUA_CMD_KEY in aqua_secrets.h == AQUAGUARD_CMD_KEY in backend/.env).
Verified against tests/cmd2_vectors.json (the gateway's C++ test builds the same line).
"""
import re
import secrets
import struct
import time

from pairing import kdf, siphash24

DEV_FLOW, DEV_RAIN = 1, 2
OPS = "ANRPC"
CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"       # Crockford base32 (aqua_join.h); reading rules O->0, I/L->1


class CommandError(ValueError):
    """Bad input for a command (maps to HTTP 400)."""


def parse_cmd_key(hexstr: str):
    """16 key bytes, or None when missing/malformed/all-zero (the backend must then refuse to send commands)."""
    if not re.fullmatch(r"[0-9a-fA-F]{32}", (hexstr or "").strip()):
        return None
    k = bytes.fromhex(hexstr.strip())
    return None if k == bytes(16) else k


def chacha20_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    def rotl(v, n):
        return ((v << n) & 0xFFFFFFFF) | (v >> (32 - n))
    st = list(struct.unpack("<4I", b"expand 32-byte k")) + list(struct.unpack("<8I", key)) + [counter & 0xFFFFFFFF] + list(struct.unpack("<3I", nonce))
    x = st[:]

    def qr(a, b, c, d):
        x[a] = (x[a] + x[b]) & 0xFFFFFFFF; x[d] = rotl(x[d] ^ x[a], 16)
        x[c] = (x[c] + x[d]) & 0xFFFFFFFF; x[b] = rotl(x[b] ^ x[c], 12)
        x[a] = (x[a] + x[b]) & 0xFFFFFFFF; x[d] = rotl(x[d] ^ x[a], 8)
        x[c] = (x[c] + x[d]) & 0xFFFFFFFF; x[b] = rotl(x[b] ^ x[c], 7)
    for _ in range(10):
        qr(0, 4, 8, 12); qr(1, 5, 9, 13); qr(2, 6, 10, 14); qr(3, 7, 11, 15)
        qr(0, 5, 10, 15); qr(1, 6, 11, 12); qr(2, 7, 8, 13); qr(3, 4, 9, 14)
    return struct.pack("<16I", *[(x[i] + st[i]) & 0xFFFFFFFF for i in range(16)])


def chacha20_xor(key: bytes, nonce: bytes, data: bytes, counter: int = 0) -> bytes:
    out = bytearray()
    for i in range(0, len(data), 64):
        ks = chacha20_block(key, counter + i // 64, nonce)
        out += bytes(a ^ b for a, b in zip(data[i:i + 64], ks))
    return bytes(out)


def enc_key(site: bytes) -> bytes:
    return kdf(site, "E", 0, 0) + kdf(site, "E", 1, 0)


def cmd_tag(site: bytes, body: str) -> str:
    return struct.pack("<Q", siphash24(kdf(site, "C", 0, 0), body.encode())).hex()


def normalize_code(code: str) -> str:
    """User input ('rpz1-6dgp-za5q', spaces, O/I/L typos) -> the 12 canonical symbols, or CommandError (same rules as aquaCodeNormalize)."""
    out = []
    for ch in (code or ""):
        if ch in "- _":
            continue
        ch = ch.upper()
        ch = "0" if ch == "O" else "1" if ch in "IL" else ch
        if ch not in CODE_ALPHABET:
            raise CommandError("bad_code")
        out.append(ch)
    if len(out) != 12:
        raise CommandError("bad_code")
    return "".join(out)


def normalize_mac(mac: str) -> str:
    m = re.sub(r"[:\-\s.]", "", (mac or "")).lower()
    if not re.fullmatch(r"[0-9a-f]{12}", m):
        raise CommandError("bad_mac")
    return m


def build_aqc2(site: bytes, *, op: str, dev_type: int, number: int = 0, mac: str | None = None, ppl_x10: int | None = None, flags: int = 0,
               name: str | None = None, code: str | None = None, ts: int | None = None, nonce: bytes | None = None) -> str:
    if op not in OPS:
        raise CommandError("bad_op")
    if dev_type not in (DEV_FLOW, DEV_RAIN):
        raise CommandError("bad_type")
    if not 0 <= number <= 250:
        raise CommandError("bad_number")
    nonce = nonce if nonce is not None else secrets.token_bytes(12)
    if len(nonce) != 12:
        raise CommandError("bad_nonce")
    ts = int(time.time()) if ts is None else int(ts)
    mac_f = normalize_mac(mac) if mac else "-"
    if name:
        nb = name.encode("utf-8")
        if len(nb) > 24 or any(b < 0x20 or b == 0x7F for b in nb):
            raise CommandError("bad_name")
        name_f = nb.hex()
    else:
        name_f = "-"
    if code:
        plain = normalize_code(code).encode()
        code_f = chacha20_xor(enc_key(site), nonce, plain).hex()
    else:
        code_f = "-"
    cal = f"{int(ppl_x10)} {int(flags)}" if ppl_x10 is not None else "- -"
    body = f"AQC2 {ts} {nonce.hex()} {op} {dev_type} {number} {mac_f} {cal} {name_f} {code_f}"
    return f"{body} {cmd_tag(site, body)}"


def parse_aqc2(site: bytes, line: str) -> dict:
    """Verify + decrypt (used by tests and diagnostics; the gateway does the same in C++)."""
    f = line.strip().split(" ")
    if len(f) != 12 or f[0] != "AQC2":
        raise CommandError("format")
    body = line.strip().rsplit(" ", 1)[0]
    if cmd_tag(site, body) != f[11]:
        raise CommandError("tag")
    nonce = bytes.fromhex(f[2])
    out = {"ts": int(f[1]), "nonce": nonce.hex(), "op": f[3], "type": int(f[4]), "number": int(f[5]),
           "mac": None if f[6] == "-" else f[6], "ppl": None if f[7] == "-" else int(f[7]), "flags": None if f[8] == "-" else int(f[8]),
           "name": None if f[9] == "-" else bytes.fromhex(f[9]).decode(), "code": None}
    if f[10] != "-":
        out["code"] = chacha20_xor(enc_key(site), nonce, bytes.fromhex(f[10])).decode()
    return out
