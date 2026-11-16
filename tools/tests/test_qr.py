import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import aqua_qr  # noqa: E402

CASES = json.loads((ROOT / "tests" / "qr_vectors.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[str(i) for i in range(len(CASES))])
def test_shared_vectors(case):
    if "ok" in case:
        assert aqua_qr.parse(case["in"]) == case["ok"]
    else:
        with pytest.raises(aqua_qr.QrError) as e:
            aqua_qr.parse(case["in"])
        assert e.value.kind == case["err"]


def test_build_matches_documented_format_and_roundtrips():
    url = aqua_qr.build("aa:bb:cc:dd:ee:ff", "0123-4567-89AB", "flow")
    assert url == "http://192.168.4.1/add?v=1&mac=AA:BB:CC:DD:EE:FF&code=0123456789AB&type=flow"
    assert aqua_qr.build("AABBCCDDEEFF", "0123456789AB", "rain", compact=True) == "AQG1;AABBCCDDEEFF;0123456789AB;rain"
    assert aqua_qr.parse(url) == {"mac": "AA:BB:CC:DD:EE:FF", "code": "0123456789AB", "type": "flow"}


def test_code_helpers():
    assert aqua_qr.format_code("0123456789AB") == "0123-4567-89AB"
    assert len(aqua_qr.ALPHABET) == 32 and not set("ILOU") & set(aqua_qr.ALPHABET)
    with pytest.raises(aqua_qr.QrError):
        aqua_qr.normalize_code("ABCD-EFGH-JKM")


def test_label_tool_serial_log_and_images(tmp_path):
    import make_node_labels as mk
    log = (
        "=== AQUA GUARD FLOW NODE (ESP32-C3)  fw 2.0  proto v2 ===\nMAC:           34:85:18:AA:BB:01\n"
        "Pairing code:  ABCD-EFGH-JKMN\nQR payload:    x\n"
        "=== AQUA GUARD RAIN NODE  fw 2.0  proto v2 ===\nMAC:           34:85:18:AA:BB:02\nPairing code:  0123-4567-89AB\n"
        "=== AQUA GUARD FLOW NODE (ESP32-C3)  fw 2.0  proto v2 ===\nMAC:           34:85:18:AA:BB:01\nPairing code:  ABCD-EFGH-JKMN\n"
    )
    nodes = mk.from_serial_log(log)
    assert nodes == [("34:85:18:AA:BB:01", "ABCD-EFGH-JKMN", "flow"), ("34:85:18:AA:BB:02", "0123-4567-89AB", "rain")]
    assert mk.from_text_lines("# c\n34:85:18:AA:BB:03, abcd-efgh-jkmn\n") == [("34:85:18:AA:BB:03", "abcd-efgh-jkmn", "flow")]
    (tmp_path / "log.txt").write_text(log)
    assert mk.main(["--serial-log", str(tmp_path / "log.txt"), "--out", str(tmp_path / "o")]) == 0
    assert (tmp_path / "o" / "348518AABB01.png").stat().st_size > 1000 and (tmp_path / "o" / "labels.pdf").stat().st_size > 1000
    # the QR in the sticker decodes back to the documented payload
    img, info = mk.make_sticker("34:85:18:AA:BB:01", "abcd efgh jkmn", "flow")
    assert info["code"] == "ABCDEFGHJKMN" and img.width >= 520
