# Water test plan - pipeline "My house" (Node 1 main -> ... -> Node 4)

Use the PC IP (`http://<pc-ip>:5173` / the gateway IP). `aquaguard.local` is unreliable on Windows.
Record a video of the screen AND the pipe/tap for every step marked REC.

| # | Step | What you do | What the web must show | Wait |
|---|------|-------------|------------------------|------|
| 1 | All nodes online | Power all 4 flow nodes + rain node. Nodes page. | Every node "Online", gateway OLED 5/5, Overview "5 of 5 online". | up to 60 s after power-up |
| 2 | Calibrate with known volumes | Nodes -> "..." -> Calibrate, per flow node: run exactly 5 L into a marked bucket. | Calibration column "Calibrated" with the new K-factor; no calibration warning. | ~2 min per node |
| 3 | Learn thresholds (no-leak run) | Leak Detection -> zone -> Balance settings -> Learn. Run a normal draw (all taps shut except one) for the learn time. | Learn result shows x+ / x- and T_leak proposals; accept. State Normal. | learn time (default 10 min) |
| 4 | Balanced flow -> Normal | Open one tap downstream, no other. | Q in ~ sum Q out, D near 0, zone "Normal", usage chips "Normal". | 1 min |
| 5 REC | Simulated leak (demo mode) | Leak Detection -> Demo mode ON. Open a small bypass/tap between Node 1 and Node 2 only. | D above x+: "Suspicious" within ~1 min, then "Possible leak" (demo: 10 s T_leak, confirm after ~3 min). An alert appears. | 3-4 min |
| 6 REC | Downstream-only flow | Close the bypass. Make flow appear only at the downstream node (e.g. lift/blow the sensor, or feed only after Node 2). | "Measurement inconsistency" (violet), NOT a leak. | 1-2 min |
| 7 REC | Tap left open | Nodes -> "..." -> Usage settings -> "Demo limits (1 h)" (T_lim 30 s, T_max 60 s). Open a tap and leave it. | After 30 s "Prolonged usage" (warning); after 60 s "Continuous abnormal usage" (critical). T_flow counter and "next limit in" count on Water Monitoring. One alert per event. Close the tap: alert auto-resolves. | ~2 min |
| 8 REC | Night flow | Usage settings -> "Demo night flow (quiet hours = now)". Run a small flow. | "Night flow" (critical) after the 60 s confirm. Stop the flow: auto-resolve. | ~2 min |
| 9 REC | Resolve with a reason | Alerts -> Active -> Resolve on any open alert, pick a reason (e.g. Test) + note. | Dialog asks the reason; event moves to the History tab with times, peak, water lost, who, reason, "demo" tag; click the row for the chart; Export CSV works; Analytics -> Leak events links there. | immediate |

After the test: switch demo off (it also ends by itself after 1 h) and check Nodes page "Usage profile" says no "demo limits".

## Video checklist
- Step 1: Nodes page with all online + the gateway OLED.
- Step 5: Leak page state changing Normal -> Suspicious -> Possible leak, with the tap/bypass visible.
- Step 6: the violet "Measurement inconsistency" chip.
- Step 7/8: the usage chip and T_flow counter next to the tap; the auto-resolve when it is closed.
- Step 9: the resolve dialog, the History row and the detail chart.
- Say the time aloud at each step so the video matches the History timestamps.
