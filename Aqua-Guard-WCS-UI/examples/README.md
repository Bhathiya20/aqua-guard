# Example pipelines

`four-node-house.json` - the real 4-node bench set-up: Node 2 on the main pipe, Nodes 3, 4 and 5 on three branches (kitchen tap, shower, garden tap).
Import it on the Pipeline page (Import JSON) or draw it yourself.  The nodes must be paired on the gateway (Nodes 2-5) or the design is not valid.

## Simulating a leak (no hardware)

The simulator is a batch tool: it writes a stretch of virtual time into the database and then prints the analysis.

1. Pair nodes 2-5 (real) **or** let the simulator create them: run it once, it creates the gateway registry.
2. Import `four-node-house.json` on the Pipeline page and save/activate it (an imported valid design is active at once).
3. Run, from `Aqua-Guard-WCS-UI/backend`:

       .venv/Scripts/python.exe simulate_sensors.py --pipeline ../examples/four-node-house.json --leak-at p2 --leak-lpm 1.2 --seconds 2900 --start <UTC time ~50 min ago, e.g. 2026-11-15T11:20>

   `--leak-at p2` puts a 1.2 L/min leak on the pipe between Node 2 and the branches: the zone "After Node 2" is a *strong* zone (in = Node 2, out = Nodes 3+4+5), so the balance rule confirms it after about 30 minutes of unaccounted flow.
   Other options: `--leak-at p1/p3/p4` (a leak after a branch node: weak zone, caught by the continuous / night-flow rules, needs a longer run), `--silent 4`, `--reset 3:900`, `--unsynced`.
4. Open Leak Detection, Alerts and Pipeline -> Live.  The data is historic: after about 30 s without new samples the nodes show "unknown"/"no data" and the gateway is reported offline.  That is correct behaviour, not a bug.
