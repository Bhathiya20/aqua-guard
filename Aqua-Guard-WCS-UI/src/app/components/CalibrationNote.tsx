// "Node 1 calibrated at 14:05 (480 p/L): earlier values use 450 p/L" - shown where volumes are listed, until the admin recalculates the history.
import { fmtClock, usePolling } from "../lib/usePolling";
import { nodeTitle, v2 } from "../lib/v2";

export function CalibrationNote() {
  const nodes = usePolling(() => v2.nodes(), 15000);
  const rows = (nodes.data?.nodes ?? []).filter((n) => n.type === "flow" && n.calibration?.earlierUsesOld && n.calibration.at);
  if (rows.length === 0) return null;
  return (
    <div className="space-y-0.5 text-xs text-amber-800" data-testid="calibration-note">
      {rows.map((n) => <p key={n.id}>{nodeTitle(n)} calibrated at {fmtClock(n.calibration!.at)} ({n.calibration!.ppl} p/L): earlier values use {n.calibration!.earlierPpl} p/L. An admin can recalculate the history in Nodes.</p>)}
    </div>
  );
}
