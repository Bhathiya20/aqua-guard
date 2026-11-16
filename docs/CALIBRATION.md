# Calibrating flow nodes (pulses per litre)

Nodes -> **Calibrate nodes** (administrators). Several nodes are calibrated **in series**: the same water passes through all of them, so
one pour gives one result per node.

## Physical set-up
Supply tap -> Node A -> Node B -> ... -> hose into a **measuring container** (marked bucket, jug, or a scale: 1 kg = 1 L).
Arrows on the sensors point along the flow; keep the hoses full (no air loops), no branches, no leaks. The wizard draws this diagram and
creates an **inactive** pipeline "Calibration rig <time>" (Pipeline page) with the same layout. That pipeline is never activated by the wizard.

## Procedure (wizard steps)
1. **Select nodes** and put them in rig order (drag or arrows; the order only changes the diagram / pipeline, not the maths).
   Starting the session **pauses leak detection** for the zones that use these nodes (banner on every page, zone status "calibrating").
2. **Connect** as shown.
3. **Purge air**: run water for about 10 s, close the tap, wait until every node shows *idle*.
4. **Runs** (3-5, each **>= 2 L**, one slow, one medium, one fast):
   * **Start** is enabled only when every node is idle (its two newest readings have a rising sequence number, the same pulse counter and are recent).
     The backend stores each node's cumulative counter and boot id.
   * Pour, close the tap, press **Stop**. The run finishes only when each node has sent **two NEW readings (measured after the click, increasing seq)
     with the same counter**. A node that sends nothing is *not* idle: the page says "waiting for Node X"; after 60 s that node is invalid for this run.
   * Enter the volume you measured (L). A bad run can be deleted.
5. **Results**: per run and node pulses, pulses/L and flow; per node the volume-weighted value (sum of pulses / sum of litres), the median, the spread and a
   quality (good <= 2 %, ok <= 5 %, poor > 5 %). **Calibrate** sends the value with the signed calibrate command; each node shows *sent -> ok*.
   Detection resumes when the result has been sent (or when you cancel).

## Rules the backend enforces
* Only **cumulative counters** are used (`pulses = cum_end - cum_start`, same boot): lost packets never change a result.
* A node **reboot during a run** (another boot id, counter back to 0) **invalidates that run**.
* Warnings (nodes are never compared with each other: uncalibrated sensors legitimately differ): a node's spread across runs > 5 %;
  per run, each node's pulses/L divided by **its own mean over all valid runs**: if **all** nodes deviate the same way by more than 3 % ->
  "volume for run N may be wrong"; if **one** node deviates by more than 3 % while the others do not -> "Node X run N: air or leak at this node";
  a run under 2 L; fewer than 3 valid runs.
* Applying refuses values outside **300-700 pulses/L**, and asks for an extra confirmation when the new value differs **more than 15 %** from the current one.
  The value is rounded to the firmware resolution (0.1 pulses/L). Default method: volume-weighted; tick "use the median" to use the median of the runs instead.
* A session that is forgotten (no activity for 4 h) expires and stops pausing leak detection. One session at a time.
* Admin only; other users can watch the session read-only.

## History, revert, manual fine-tune
Nodes -> "..." -> Calibrate shows the node's calibration history (date, source wizard / manual / revert, runs, spread, old -> new) with
**Revert to <old value>**. The manual field still sends any value 10-6000 and is recorded in the history as "manual".

## API (for reference)
`POST /api/calibration/sessions {nodeIds}` - `GET /api/calibration/sessions/<id>` - `POST .../runs` - `POST .../runs/<r>/stop` - `PUT .../runs/<r> {volumeL}` -
`DELETE .../runs/<r>` - `POST .../apply {method, confirm, nodeIds?}` - `POST .../cancel` - `GET /api/calibration/active` - `GET /api/nodes/<id>/calibrations` -
`POST /api/nodes/<id>/calibrations/<h>/revert`. Database: migration 11 (`calibration_sessions`, `calibration_session_nodes`, `calibration_runs`,
`calibration_run_nodes`, `calibration_history`). No firmware change.

## Presets and the Leak Detection page
Pipelines -> **New from preset** creates inactive example networks (Calibration rig, Leak bench, Branch balance, Single meter house, My house) with a
description of what each tests and how to build it. A calibration rig is never used for leak detection (the Leak Detection page does not offer it and the
backend refuses to activate it).
The Leak Detection page shows for every zone the inflow node, the outflow node(s), the live flows, `Q_in - sum(Q_out) = unaccounted`, the tolerance
and the rule progress, plus a table of the nodes and the zones each one feeds.
