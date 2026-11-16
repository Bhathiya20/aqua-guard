#!/usr/bin/env python3
"""Capture several serial ports at once with a common timestamp (e.g. node + gateway while pairing).

  python tools/serial_capture.py gateway=COM29 node=COM35 --seconds 60 --out logs/pairing.txt

Every line is printed and written as   HH:MM:SS.mmm [label] text   so the two devices can be read side by side.
DTR/RTS are NOT toggled while opening, so an ESP32 DevKit is not reset by the capture (the C3's USB-CDC is not either).
Close the Arduino IDE / `arduino-cli monitor` first: a port can only be opened by one program.
Needs:  pip install pyserial   (see tools/requirements.txt)
"""
import argparse
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import serial


def main(argv=None) -> int:
    for st in (sys.stdout, sys.stderr):                              # a Windows console (cp1252) must not crash on odd bytes
        try: st.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass                                      # noqa: BLE001
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ports", nargs="+", help="label=COMx (e.g. gateway=COM29 node=COM35)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--seconds", type=float, default=60, help="0 = until Ctrl+C")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)

    out = None
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        out = open(a.out, "w", encoding="utf-8")
    lock = threading.Lock()
    stop = threading.Event()

    def emit(label, text):
        line = f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]} [{label}] {text}"
        with lock:
            print(line, flush=True)
            if out:
                out.write(line + "\n"); out.flush()

    def reader(label, port):
        try:
            s = serial.Serial(); s.port = port; s.baudrate = a.baud; s.timeout = 0.2; s.dtr = False; s.rts = False; s.open()
        except Exception as e:                                       # noqa: BLE001
            emit(label, f"!! cannot open {port}: {e}"); return
        emit(label, f"-- capturing {port} @ {a.baud}")
        buf = b""
        while not stop.is_set():
            try:
                chunk = s.read(256)
            except Exception as e:                                   # noqa: BLE001  (USB-CDC re-enumerates on reset)
                emit(label, f"!! port lost: {e}"); break
            buf += chunk
            while b"\n" in buf:
                ln, buf = buf.split(b"\n", 1)
                emit(label, ln.decode("utf-8", "replace").rstrip("\r"))
        s.close()

    threads = []
    for spec in a.ports:
        if "=" not in spec:
            ap.error("ports must look like label=COMx")
        label, port = spec.split("=", 1)
        t = threading.Thread(target=reader, args=(label, port), daemon=True); t.start(); threads.append(t)
    try:
        t0 = time.time()
        while not (a.seconds and time.time() - t0 >= a.seconds):
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    stop.set()
    for t in threads:
        t.join(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
