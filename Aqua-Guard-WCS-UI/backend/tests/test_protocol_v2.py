"""AQC2 (signed + encrypted backend -> gateway command) must reproduce the shared vector with the gateway's C++ implementation."""
import json
from pathlib import Path

import pytest

import protocol_v2 as p

VEC = json.loads((Path(__file__).resolve().parents[3] / "tests" / "cmd2_vectors.json").read_text(encoding="utf-8"))
SITE = bytes.fromhex(VEC["siteKeyHex"])
NONCE = bytes.fromhex("0102030405060708090a0b0c")


def test_reproduces_the_shared_vector_exactly():
    pl = VEC["plain"]
    line = p.build_aqc2(SITE, op=pl["op"], dev_type=pl["type"], number=pl["number"], mac=pl["mac"], name=pl["name"], code=pl["code"], ts=1760000000, nonce=NONCE)
    assert line == VEC["line"]


def test_parse_roundtrip_decrypts_the_code():
    d = p.parse_aqc2(SITE, VEC["line"])
    assert d["op"] == "A" and d["mac"] == "447b30854b05" and d["name"] == "Kitchen" and d["code"] == "RPZ16DGPZA5Q"


def test_code_never_appears_in_clear():
    line = p.build_aqc2(SITE, op="A", dev_type=1, mac="447b30854b05", code="RPZ1-6DGP-ZA5Q", ts=1760000000, nonce=NONCE)
    assert "RPZ1" not in line and "ZA5Q" not in line and "RPZ16DGPZA5Q".encode().hex() not in line


def test_tampering_breaks_the_tag():
    bad = VEC["line"].replace(" A 1 0 ", " A 2 0 ")
    with pytest.raises(p.CommandError):
        p.parse_aqc2(SITE, bad)
    with pytest.raises(p.CommandError):
        p.parse_aqc2(bytes(16), VEC["line"])


def test_each_op_builds_and_parses():
    for kw in ({"op": "N", "number": 2, "name": "Garden"}, {"op": "R", "number": 3}, {"op": "C", "number": 1, "ppl_x10": 4500, "flags": 1},
               {"op": "P", "number": 1, "mac": "AA:BB:CC:00:11:22", "code": "abcd-efgh-jkmn"}):
        line = p.build_aqc2(SITE, dev_type=1, ts=1760000000, **kw)
        d = p.parse_aqc2(SITE, line)
        assert d["op"] == kw["op"] and d["number"] == kw.get("number", 0)
    assert p.parse_aqc2(SITE, p.build_aqc2(SITE, op="C", dev_type=1, number=1, ppl_x10=4500, flags=1, ts=5))["ppl"] == 4500


def test_code_reading_rules_match_the_firmware():
    assert p.normalize_code("rpz1-6dgp-za5q") == "RPZ16DGPZA5Q"
    assert p.normalize_code("RPZl 6DGP ZA5Q") == "RPZ16DGPZA5Q"       # I/L -> 1
    for bad in ("", "RPZ1-6DGP", "RPZ1-6DGP-ZA5Q-9", "RPZ1-6DGP-ZA5U"):   # U is not in the alphabet
        with pytest.raises(p.CommandError):
            p.normalize_code(bad)


def test_bad_fields_are_refused():
    with pytest.raises(p.CommandError):
        p.build_aqc2(SITE, op="X", dev_type=1)
    with pytest.raises(p.CommandError):
        p.build_aqc2(SITE, op="A", dev_type=3, mac="447b30854b05", code="RPZ16DGPZA5Q")
    with pytest.raises(p.CommandError):
        p.build_aqc2(SITE, op="A", dev_type=1, mac="nothex", code="RPZ16DGPZA5Q")
    with pytest.raises(p.CommandError):
        p.build_aqc2(SITE, op="N", dev_type=1, number=1, name="x" * 25)


def test_command_key_guard():
    assert p.parse_cmd_key("") is None and p.parse_cmd_key("zz" * 16) is None and p.parse_cmd_key("00" * 16) is None and p.parse_cmd_key("11" * 15) is None
    assert p.parse_cmd_key("11" * 16) == bytes([0x11]) * 16


def test_chacha20_matches_rfc8439():
    key = bytes(range(32))
    blk = p.chacha20_block(key, 1, bytes.fromhex("000000090000004a00000000"))          # RFC 8439 section 2.3.2
    assert blk[:16].hex() == "10f1e7e4d13b5915500fdd1fa32071c4"
    pt = b"Ladies and Gentlemen of the class of '99: If I could offer you only one tip for the future, sunscreen would be it."
    ct = p.chacha20_xor(key, bytes.fromhex("000000000000004a00000000"), pt, counter=1)  # section 2.4.2
    assert ct[:16].hex() == "6e2e359a2568f98041ba0728dd0d6981"
    assert p.chacha20_xor(key, bytes.fromhex("000000000000004a00000000"), ct, counter=1) == pt
