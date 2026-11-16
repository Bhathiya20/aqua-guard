#!/usr/bin/env python3
"""Reset the gateway (RTS pulse, nothing is flashed) N times and time how long a node needs to join it again.

  python tools/gw_reset_rejoin_test.py --gw COM29 --node COM37 --label "Node 2" --runs 3

The gateway is reset with the USB-UART RTS line (EN).  A node counts as back when its unacknowledged frames (pend=) pile up while the gateway
is away and then fall back to <= 1: the gateway is ACKing it again, i.e. it re-joined with a fresh session.  NOTE: opening the node's USB port
may reboot a C3 once at the start; that is why the first run starts after a short wait.
Also needs the node's serial only to print what it does; pass --node "" to skip.
"""
import argparse, re, sys, threading, time
import serial


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gw", default="COM29"); ap.add_argument("--node", default="COM37")
    ap.add_argument("--match", default=r"\[PAIR\] flow 2 joined", help="regex of the gateway line that means 'the node is back'")
    ap.add_argument("--runs", type=int, default=3); ap.add_argument("--limit", type=float, default=60.0)
    ap.add_argument("--settle", type=float, default=25.0, help="seconds to wait between runs")
    a = ap.parse_args()
    gw = serial.Serial(a.gw, 115200, timeout=0.2)
    node = serial.Serial(a.node, 115200, timeout=0.2) if a.node else None
    lines = []
    stop = threading.Event()

    def reader(port, tag):
        buf = b""
        while not stop.is_set():
            try: d = port.read(512)
            except Exception: break
            if not d: continue
            buf += d
            while b"\n" in buf:
                l, buf = buf.split(b"\n", 1)
                lines.append((time.time(), tag, l.decode("utf-8", "replace").strip()))
    for p, t in ((gw, "gw"), (node, "node")):
        if p: threading.Thread(target=reader, args=(p, t), daemon=True).start()
    rx = re.compile(a.match)
    results = []
    for i in range(1, a.runs + 1):
        time.sleep(a.settle if i > 1 else 3)
        mark = len(lines)
        gw.dtr = False; gw.rts = True; time.sleep(0.15); gw.rts = False        # EN low -> high = hard reset
        t0 = time.time(); back = None
        while time.time() - t0 < a.limit + 30:
            peak = 0
            for tt, tag, l in lines[mark:]:
                if tag != "node" or tt < t0: continue
                m = re.search(r"pend=(\d+)", l)
                if not m: continue
                pend = int(m.group(1)); peak = max(peak, pend)
                if peak >= 2 and pend <= 1: back = tt - t0; break      # the backlog built up while the gateway was away is ACKed again = link is back
            if back is not None: break
            time.sleep(0.2)
        results.append(back)
        print(f"run {i}: node back after {back:.1f} s" if back is not None else f"run {i}: NOT back within {a.limit + 30:.0f} s")
        for tt, tag, l in lines[mark:]:
            if tag == "node" and re.search(r"LINK|PAIR|JOIN|SCAN|PAIRED", l):
                print(f"   +{tt - t0:5.1f}s [{tag}] {l[:150]}")
                if tt - t0 > (back or 999) + 3: break
    stop.set()
    ok = all(r is not None and r <= a.limit for r in results)
    print("RESULT:", "PASS" if ok else "FAIL", results)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
