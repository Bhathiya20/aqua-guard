#!/usr/bin/env python3
"""Printable QR stickers for Aqua Guard nodes (QR + MAC + pairing code in plain text, for manual entry).

Input (any of):
  --mac AA:BB:CC:DD:EE:FF --code ABCD-EFGH-JKMN [--type flow|rain]       one node
  --file nodes.txt                                                        many nodes, one per line:  MAC CODE [type]
  --serial-log boot.txt                                                   raw serial output captured from the nodes
                                                                          (uses their "MAC:" and "Pairing code:" lines)

Output (default ./labels): one PNG per node + labels.pdf with all of them.

  pip install -r tools/requirements.txt
  python tools/make_node_labels.py --serial-log boot.txt

The QR holds the URL form (a phone camera opens the gateway portal's add-node page with the fields filled in);
the printed MAC and code are the fallback when the QR cannot be scanned.  The MAC alone is never enough to pair:
the code is the secret, so keep the stickers on the nodes, not in public.
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aqua_qr  # noqa: E402


def from_text_lines(text: str):
    """Lines 'MAC CODE [type]' (comma/space separated, '#' comments)."""
    out = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [p for p in re.split(r"[,\s;]+", line) if p]
        if len(parts) < 2:
            raise SystemExit(f"cannot read line: {raw!r} (expected: MAC CODE [type])")
        out.append((parts[0], parts[1], parts[2].lower() if len(parts) > 2 else "flow"))
    return out


def from_serial_log(text: str):
    """Pairs a 'MAC:' line with the next 'Pairing code:' line (the node banner).  Later boots of one node repeat the
    same pair, so duplicates are dropped."""
    out, mac = [], None
    typ = "flow"
    for line in text.splitlines():
        if "RAIN NODE" in line.upper():
            typ = "rain"
        elif "FLOW NODE" in line.upper():
            typ = "flow"
        m = re.match(r"\s*MAC:\s*([0-9A-Fa-f:\-]{17})", line)
        if m:
            mac = m.group(1)
            continue
        m = re.match(r"\s*Pairing code:\s*([0-9A-Za-z\- ]+)", line)
        if m and mac:
            item = (mac, m.group(1).strip(), typ)
            if item not in out:
                out.append(item)
            mac = None
    return out


def make_sticker(mac: str, code: str, typ: str):
    import qrcode
    from PIL import Image, ImageDraw, ImageFont

    info = aqua_qr.parse(aqua_qr.build(mac, code, typ))            # validates + normalises
    payload = aqua_qr.build(info["mac"], info["code"], info["type"])
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=3)
    qr.add_data(payload); qr.make(fit=True)
    qimg = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    try:
        big, small = ImageFont.load_default(size=30), ImageFont.load_default(size=22)
    except TypeError:                                                # very old Pillow: fixed-size bitmap font
        big = small = ImageFont.load_default()
    w = max(qimg.width, 520)
    img = Image.new("RGB", (w, qimg.height + 150), "white")
    d = ImageDraw.Draw(img)
    img.paste(qimg, ((w - qimg.width) // 2, 0))
    y = qimg.height + 6
    title = "Aqua Guard - " + ("Rain node" if info["type"] == "rain" else "Flow node")
    d.text((w // 2, y), title, fill="black", font=big, anchor="mt")
    d.text((w // 2, y + 44), "MAC  " + info["mac"], fill="black", font=small, anchor="mt")
    d.text((w // 2, y + 76), "CODE " + aqua_qr.format_code(info["code"]), fill="black", font=big, anchor="mt")
    return img, info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mac"); ap.add_argument("--code"); ap.add_argument("--type", default="flow", choices=["flow", "rain"])
    ap.add_argument("--file"); ap.add_argument("--serial-log")
    ap.add_argument("--out", default="labels")
    a = ap.parse_args(argv)

    nodes = []
    if a.mac or a.code:
        if not (a.mac and a.code):
            ap.error("--mac and --code go together")
        nodes.append((a.mac, a.code, a.type))
    if a.file:
        nodes += from_text_lines(Path(a.file).read_text(encoding="utf-8", errors="replace"))
    if a.serial_log:
        nodes += from_serial_log(Path(a.serial_log).read_text(encoding="utf-8", errors="replace"))
    if not nodes:
        ap.error("no nodes given (use --mac/--code, --file or --serial-log)")

    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    pages = []
    for mac, code, typ in nodes:
        try:
            img, info = make_sticker(mac, code, typ)
        except aqua_qr.QrError as e:
            print(f"skipped {mac} {code}: {e}", file=sys.stderr)
            continue
        name = info["mac"].replace(":", "") + ".png"
        img.save(out / name)
        pages.append(img)
        print(f"wrote {out / name}")
    if not pages:
        return 1
    from PIL import Image
    Image.init()                                                     # registers the JPEG writer that the PDF plugin needs
    pages[0].save(out / "labels.pdf", save_all=True, append_images=pages[1:], resolution=150)
    print(f"wrote {out / 'labels.pdf'} ({len(pages)} sticker(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
